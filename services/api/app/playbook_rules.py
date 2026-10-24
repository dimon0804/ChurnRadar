"""Сценарии удержания без базы: правило, контрольная группа, лимиты, оценка эффекта.

Здесь только чистые функции, чтобы их можно было проверить юнит-тестами. Работа с базой,
исполнитель и HTTP — в app/playbooks.py.

Контрольная группа выбирается по хешу id клиента, без соли сценария. Так клиент из контроля
остаётся в контроле во всех сценариях сразу и не получает письмо «с другой стороны», а
контроль 10% — часть контроля 20%. Предпросмотр нового сценария поэтому точен: id сценария
для разбиения не нужен.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LEVELS = ("high", "medium", "low", "unknown")
HOLDOUT_SALT = b"churnradar-holdout:"
DEFAULT_CONTROL_SHARE = 0.2
MAX_CONTROL_SHARE = 0.5
# меньше стольких клиентов в группе — честно говорим «мало данных»
MIN_GROUP = 30
WINDOWS = (0, 7, 14, 30)
Z95 = 1.959964

# Причины риска (коды признаков riskcore) — для условия «главная причина».
REASONS: dict[str, str] = {
    "insufficient_funds": "Не хватит денег на продление",
    "autoprolong_off_expiring": "Автопродление выключено, срок подходит",
    "prolong_wait": "Услуга ждёт оплаты",
    "suspended": "Сервер выключен",
    "failed_payments": "Платежи не проходят",
    "inactivity": "Давно не пополнял",
    "churn_history": "Уже уходил раньше",
    "short_commitment": "Платит короткими периодами",
    "bonus_dependency": "Держится на бонусах",
    "withdraw_pressure": "Выводит деньги",
}


# --- модель правила -----------------------------------------------------------------------


class Conditions(BaseModel):
    """Условия срабатывания; заданные условия должны выполняться все сразу."""

    model_config = ConfigDict(extra="forbid")
    risk_levels: list[Literal["high", "medium", "low", "unknown"]] | None = Field(
        default=None, min_length=1, max_length=4
    )
    became_high: bool = False
    main_reason: str | None = Field(default=None, max_length=64)
    min_expected_loss: int | None = Field(default=None, ge=0, le=10**12)
    no_email_days: int | None = Field(default=None, ge=1, le=365)
    min_mrr: int | None = Field(default=None, ge=0, le=10**12)
    max_mrr: int | None = Field(default=None, ge=0, le=10**12)
    autoprolong_off: bool = False
    expires_within_days: int | None = Field(default=None, ge=0, le=90)

    @model_validator(mode="after")
    def _check(self) -> Conditions:
        if self.main_reason is not None and self.main_reason not in REASONS:
            raise ValueError(f"Неизвестная причина риска: {self.main_reason}")
        if (
            self.min_mrr is not None
            and self.max_mrr is not None
            and self.min_mrr > self.max_mrr
        ):
            raise ValueError("Нижняя граница MRR больше верхней")
        if not any(
            (
                self.risk_levels,
                self.became_high,
                self.main_reason,
                self.min_expected_loss is not None,
                self.no_email_days is not None,
                self.min_mrr is not None,
                self.max_mrr is not None,
                self.autoprolong_off,
                self.expires_within_days is not None,
            )
        ):
            raise ValueError(
                "Задайте хотя бы одно условие, иначе сценарий заденет всех клиентов"
            )
        return self


class EmailAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["email"]
    # None — шаблон по главной причине клиента, как в рассылке
    template_code: str | None = Field(default=None, max_length=64)


class StatusAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["status"]
    status: Literal["snoozed", "resolved"]
    days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def _days(self) -> StatusAction:
        if self.status == "snoozed" and self.days is None:
            raise ValueError("Чтобы отложить, укажите, на сколько дней")
        if self.status == "resolved" and self.days is not None:
            raise ValueError("Для «Решено» срок не нужен")
        return self


class NotifyAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["notify"]
    username: str = Field(min_length=1, max_length=64)


Action = Annotated[
    EmailAction | StatusAction | NotifyAction, Field(discriminator="type")
]


class PlaybookIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool = False
    mode: Literal["simulation", "live"] = "simulation"
    conditions: Conditions
    actions: list[Action] = Field(min_length=1, max_length=3)
    control_share: float = Field(
        default=DEFAULT_CONTROL_SHARE, ge=0.0, le=MAX_CONTROL_SHARE
    )
    max_per_day: int = Field(default=50, ge=1, le=5000)
    cooldown_days: int = Field(default=14, ge=1, le=365)
    preset: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _actions(self) -> PlaybookIn:
        kinds = [a.type for a in self.actions]
        if len(kinds) != len(set(kinds)):
            raise ValueError("Каждое действие можно указать один раз")
        return self

    @property
    def needs_email(self) -> bool:
        return any(a.type == "email" for a in self.actions)


# --- кандидаты и условия -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    customer_id: int
    email: str
    risk_level: str
    risk_score: float | None
    expected_loss: int
    monthly_revenue: int
    balance: int
    total_replenished: int
    main_reason: str | None = None
    prev_level: str | None = None
    autoprolong_off: bool = False
    min_days_to_expiry: float | None = None
    last_email_at: datetime | None = None


def matches(
    cond: Conditions, c: Candidate, now: datetime, *, levels_ready: bool = True
) -> bool:
    """Подходит ли клиент под правило. levels_ready=False — уровней с прошлого раза ещё нет."""
    if cond.risk_levels and c.risk_level not in cond.risk_levels:
        return False
    if cond.became_high and not (
        levels_ready and c.risk_level == "high" and c.prev_level != "high"
    ):
        return False
    if cond.main_reason and c.main_reason != cond.main_reason:
        return False
    if cond.min_expected_loss is not None and c.expected_loss < cond.min_expected_loss:
        return False
    if cond.min_mrr is not None and c.monthly_revenue < cond.min_mrr:
        return False
    if cond.max_mrr is not None and c.monthly_revenue > cond.max_mrr:
        return False
    if cond.autoprolong_off and not c.autoprolong_off:
        return False
    if cond.expires_within_days is not None and (
        c.min_days_to_expiry is None
        or not 0 <= c.min_days_to_expiry <= cond.expires_within_days
    ):
        return False
    if cond.no_email_days is not None and c.last_email_at is not None:
        if c.last_email_at > now - timedelta(days=cond.no_email_days):
            return False
    return True


def holdout_point(customer_id: int) -> float:
    """Точка клиента на [0, 1): одна и та же при любом запуске и в любом процессе."""
    digest = hashlib.sha256(HOLDOUT_SALT + str(customer_id).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def in_control(customer_id: int, share: float) -> bool:
    return share > 0 and holdout_point(customer_id) < share


@dataclass(slots=True)
class Selection:
    matched: int = 0
    treated: list[Candidate] = field(default_factory=list)
    control: list[Candidate] = field(default_factory=list)
    skipped_cooldown: int = 0
    skipped_limit: int = 0
    skipped_no_email: int = 0


def select(
    candidates: Iterable[Candidate],
    *,
    share: float,
    cooldown: set[int],
    treated_today: int,
    max_per_day: int,
    needs_email: bool,
) -> Selection:
    """Кого обработать сейчас. candidates — уже подходящие под условия.

    Порядок — по ожидаемой потере. Дневной лимит считает только тех, кому действие
    выполняется, но, упёршись в него, мы останавливаем весь проход: контроль набирается из
    того же начала списка, что и затронутые, иначе группы были бы несравнимы.
    """
    ordered = sorted(candidates, key=lambda c: (-c.expected_loss, c.customer_id))
    sel = Selection(matched=len(ordered))
    budget = max(0, max_per_day - treated_today)
    for i, c in enumerate(ordered):
        if c.customer_id in cooldown:
            sel.skipped_cooldown += 1
            continue
        if needs_email and not c.email:
            # без адреса клиент не годится ни в одну группу — иначе контроль был бы «шире»
            sel.skipped_no_email += 1
            continue
        if in_control(c.customer_id, share):
            sel.control.append(c)
            continue
        if len(sel.treated) >= budget:
            rest = ordered[i:]
            sel.skipped_limit = sum(1 for r in rest if r.customer_id not in cooldown)
            break
        sel.treated.append(c)
    return sel


# --- эффект -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    risk_down: bool
    topped_up: bool
    retained: bool


METRICS: tuple[tuple[str, str], ...] = (
    ("retained", "Не ушли"),
    ("risk_down", "Риск снизился"),
    ("topped_up", "Пополнили баланс"),
)


def diff_interval(x1: int, n1: int, x2: int, n2: int) -> tuple[float, float, float]:
    """Разница долей x1/n1 − x2/n2 и 95% интервал Агрести — Каффо.

    К каждой группе добавляем по одному успеху и неудаче: при долях 0 или 1 обычный
    интервал Вальда схлопывается в точку и обещает уверенность, которой нет.
    """
    p1, p2 = x1 / n1, x2 / n2
    a1, a2 = (x1 + 1) / (n1 + 2), (x2 + 1) / (n2 + 2)
    se = math.sqrt(a1 * (1 - a1) / (n1 + 2) + a2 * (1 - a2) / (n2 + 2))
    center = a1 - a2
    return p1 - p2, max(-1.0, center - Z95 * se), min(1.0, center + Z95 * se)


def verdict(enough: bool, low: float, high: float) -> str:
    if not enough:
        return "few_data"
    if low > 0:
        return "better"
    if high < 0:
        return "worse"
    return "no_effect"


def compare(
    treated: Sequence[Outcome], control: Sequence[Outcome], min_group: int = MIN_GROUP
) -> dict[str, Any]:
    """Сравнение затронутых и контроля по трём исходам."""
    n1, n2 = len(treated), len(control)
    enough = n1 >= min_group and n2 >= min_group
    metrics = []
    for code, title in METRICS:
        x1 = sum(1 for o in treated if getattr(o, code))
        x2 = sum(1 for o in control if getattr(o, code))
        row: dict[str, Any] = {
            "code": code,
            "title": title,
            "treated": x1,
            "control": x2,
            "treated_share": round(x1 / n1, 4) if n1 else None,
            "control_share": round(x2 / n2, 4) if n2 else None,
            "diff": None,
            "ci_low": None,
            "ci_high": None,
            "verdict": "few_data",
        }
        if n1 and n2:
            diff, low, high = diff_interval(x1, n1, x2, n2)
            row.update(
                diff=round(diff, 4),
                ci_low=round(low, 4),
                ci_high=round(high, 4),
                verdict=verdict(enough, low, high),
            )
        metrics.append(row)
    return {"treated_n": n1, "control_n": n2, "enough": enough, "metrics": metrics}


def explain(result: dict[str, Any], min_group: int = MIN_GROUP) -> str:
    """Одна фраза о выводе — её показывает карточка эффекта."""
    n1, n2 = result["treated_n"], result["control_n"]
    if n1 == 0 and n2 == 0:
        return "Срабатываний за этот срок ещё нет."
    if not result["enough"]:
        return (
            f"Мало данных: затронуто {n1}, в контроле {n2}. Для вывода нужно хотя бы по {min_group} "
            "клиентов в каждой группе, пока разница может быть случайной."
        )
    retained = result["metrics"][0]
    if retained["verdict"] == "better":
        return (
            f"Сценарий помогает: не ушли на {abs(retained['diff']) * 100:.1f} п.п. больше, чем в контроле "
            f"(95% интервал от {retained['ci_low'] * 100:+.1f} до {retained['ci_high'] * 100:+.1f} п.п.)."
        )
    if retained["verdict"] == "worse":
        return "В контроле ушло меньше, чем среди затронутых. Стоит пересмотреть текст письма или условие."
    return (
        "Разницы с контролем пока не видно: интервал включает ноль. Это не значит, что эффекта нет, "
        "просто его не отличить от случайности на такой выборке."
    )


# --- готовые сценарии ----------------------------------------------------------------------

PRESETS: tuple[dict[str, Any], ...] = (
    {
        "code": "funds_before_charge",
        "name": "Не хватает денег на продление",
        "description": "Письмо за 3 дня до списания, если на балансе не хватит на продление.",
        "conditions": {
            "main_reason": "insufficient_funds",
            "expires_within_days": 3,
            "no_email_days": 7,
        },
        "actions": [{"type": "email", "template_code": "insufficient_funds"}],
    },
    {
        "code": "big_autoprolong_off",
        "name": "Автопродление выключено у крупного клиента",
        "description": "Письмо клиенту и уведомление ответственному, если у клиента с MRR от €30 выключено автопродление.",
        "conditions": {"autoprolong_off": True, "min_mrr": 3000, "no_email_days": 14},
        "actions": [
            {"type": "email", "template_code": "autoprolong_off_expiring"},
            {"type": "notify", "username": "admin"},
        ],
    },
    {
        "code": "high_risk_silent",
        "name": "Высокий риск и давно не писали",
        "description": "Письмо по главной причине риска, если клиенту не писали две недели.",
        "conditions": {"risk_levels": ["high"], "no_email_days": 14},
        "actions": [{"type": "email", "template_code": None}],
    },
)
