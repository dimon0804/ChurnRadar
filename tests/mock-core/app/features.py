"""Реестр признаков модели оттока.

Каждый признак возвращает силу сигнала от 0 до 1 и объяснение для маркетолога.
Вес признака — сколько пунктов из 100 он даёт сам по себе при сигнале 1.
Чтобы добавить признак, достаточно написать функцию и зарегистрировать её в FEATURES.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from .store import Account, Service, Transaction

DAYS_IN_MONTH = 30.4375

TERM_DAYS = {
    "hour": 1 / 24,
    "week": 7.0,
    "month": DAYS_IN_MONTH,
    "quarter_year": DAYS_IN_MONTH * 3,
    "half_year": DAYS_IN_MONTH * 6,
    "year": DAYS_IN_MONTH * 12,
}


def monthly_price(service: Service) -> float:
    return service.price * DAYS_IN_MONTH / TERM_DAYS[service.payment_term]


def days_to_expiry(service: Service, now: datetime) -> float:
    return (service.expires_at - now).total_seconds() / 86400


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def human_days(days: float) -> str:
    days = abs(days)
    if days < 1:
        hours = max(1, round(days * 24))
        return f"{hours} {plural(hours, 'час', 'часа', 'часов')}"
    n = round(days)
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


@dataclass
class Context:
    account: Account
    services: List[Service]
    live: List[Service]
    transactions: List[Transaction]
    now: datetime
    horizon_days: int
    daily_cost: float
    service: Optional[Service] = None


Signal = Tuple[float, str]


@dataclass(frozen=True)
class Feature:
    code: str
    title: str
    description: str
    group: str
    level: str
    default_weight: float
    compute: Callable[[Context], Signal]
    min: float = 0.0
    max: float = 100.0


# --- признаки уровня услуги ---


def autoprolong_off_expiring(ctx: Context) -> Signal:
    s = ctx.service
    if s.auto_prolong:
        return 0.0, "Автопродление включено — услуга продлится сама."
    d = days_to_expiry(s, ctx.now)
    if d <= 0:
        return 1.0, f"Автопродление выключено, срок услуги закончился {human_days(d)} назад, а продления нет."
    if d <= ctx.horizon_days:
        value = 1 - 0.6 * d / ctx.horizon_days
        return value, (
            f"Автопродление выключено, до конца оплаченного периода {human_days(d)}: "
            "если клиент не продлит услугу вручную, она закончится."
        )
    return 0.0, f"Автопродление выключено, но до конца срока ещё {human_days(d)} — это за пределами горизонта."


def prolong_wait_status(ctx: Context) -> Signal:
    if ctx.service.status == "prolong_wait":
        return 1.0, "Услуга ждёт оплаты продления: срок вышел, а деньги так и не поступили."
    return 0.0, "Услуга не висит в ожидании оплаты."


def suspended_or_blocked(ctx: Context) -> Signal:
    status = ctx.service.status
    if status == "blocked":
        return 1.0, "Услуга заблокирована — обычно так бывает из-за неоплаты или нарушения правил."
    if status == "suspended":
        return 0.8, "Услуга приостановлена — клиент ею сейчас не пользуется."
    return 0.0, "Услуга работает в обычном режиме."


# --- признаки уровня аккаунта ---


def low_balance_coverage(ctx: Context) -> Signal:
    if ctx.daily_cost <= 0:
        return 0.0, "Платных действующих услуг нет, баланс не расходуется."
    days = ctx.account.balance / ctx.daily_cost
    if days < 7:
        value = 1.0
    elif days < 30:
        value = (30 - days) / 23
    else:
        value = 0.0
    if value == 0:
        return 0.0, f"Денег на балансе хватит примерно на {human_days(days)} — запас достаточный."
    return value, f"Денег на балансе хватит всего на {human_days(days)} работы текущих услуг."


FAILED_WINDOW_DAYS = 90


def failed_payments(ctx: Context) -> Signal:
    since = ctx.now - timedelta(days=FAILED_WINDOW_DAYS)
    failed = [t for t in ctx.transactions if t.status == "not_performed" and since <= t.created_at <= ctx.now]
    n = len(failed)
    if n == 0:
        return 0.0, f"За последние {FAILED_WINDOW_DAYS} дней все платежи прошли."
    word = plural(n, "платёж", "платежа", "платежей")
    verb = "не прошёл" if word == "платёж" else "не прошли"
    return min(1.0, n / 3), f"За последние {FAILED_WINDOW_DAYS} дней {verb} {n} {word} — возможны проблемы с картой или деньгами."


DELETIONS_WINDOW_DAYS = 180


def recent_deletions(ctx: Context) -> Signal:
    deleted = [s for s in ctx.services if not s.live]
    if not deleted:
        return 0.0, "Клиент не удалял услуги."
    since = ctx.now - timedelta(days=DELETIONS_WINDOW_DAYS)
    recent = [s for s in deleted if s.expires_at >= since]
    share = len(deleted) / len(ctx.services)
    value = max(min(1.0, len(recent) / 2), share * 0.6)
    total = len(ctx.services)
    text = f"Клиент удалил {len(deleted)} из {total} {plural(total, 'услуги', 'услуг', 'услуг')}"
    if recent:
        text += f", из них {len(recent)} — за последние полгода"
    return value, text + "."


ACTIVITY_WINDOW_DAYS = 60


def no_recent_payments(ctx: Context) -> Signal:
    since = ctx.now - timedelta(days=ACTIVITY_WINDOW_DAYS)
    paid = [t for t in ctx.transactions if t.status == "performed" and since <= t.created_at <= ctx.now]
    if not paid:
        return 1.0, f"За последние {ACTIVITY_WINDOW_DAYS} дней клиент не сделал ни одного успешного платежа."
    if len(paid) == 1:
        return 0.5, f"За последние {ACTIVITY_WINDOW_DAYS} дней был всего один успешный платёж."
    return 0.0, f"Клиент регулярно платит: {len(paid)} успешных платежей за {ACTIVITY_WINDOW_DAYS} дней."


FEATURES: List[Feature] = [
    Feature(
        "autoprolong_off_expiring",
        "Автопродление выключено, срок скоро закончится",
        "Услуга без автопродления, у которой оплаченный период заканчивается в пределах горизонта прогноза или уже закончился.",
        "renewal",
        "service",
        45,
        autoprolong_off_expiring,
    ),
    Feature(
        "prolong_wait_status",
        "Услуга ждёт оплаты продления",
        "Статус prolong_wait: срок вышел, продление не оплачено.",
        "renewal",
        "service",
        40,
        prolong_wait_status,
    ),
    Feature(
        "suspended_or_blocked",
        "Услуга приостановлена или заблокирована",
        "Статусы suspended и blocked: клиент не пользуется услугой.",
        "renewal",
        "service",
        50,
        suspended_or_blocked,
    ),
    Feature(
        "low_balance_coverage",
        "Мало денег на балансе",
        "Баланса хватит меньше чем на 30 дней работы действующих услуг; меньше 7 дней — максимальный сигнал.",
        "finance",
        "account",
        30,
        low_balance_coverage,
    ),
    Feature(
        "failed_payments",
        "Неуспешные платежи",
        "Транзакции со статусом not_performed за последние 90 дней; три и больше — максимальный сигнал.",
        "finance",
        "account",
        35,
        failed_payments,
    ),
    Feature(
        "recent_deletions",
        "Клиент удаляет услуги",
        "Удалённые услуги клиента: сколько закончилось за последние полгода и какая доля от всех услуг.",
        "behavior",
        "account",
        25,
        recent_deletions,
    ),
    Feature(
        "no_recent_payments",
        "Давно не платил",
        "Нет успешных платежей за последние 60 дней (один платёж — половина сигнала).",
        "behavior",
        "account",
        20,
        no_recent_payments,
    ),
]

FEATURES_BY_CODE: Dict[str, Feature] = {f.code: f for f in FEATURES}
