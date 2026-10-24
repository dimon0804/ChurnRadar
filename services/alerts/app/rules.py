"""Правила оповещений и тексты сообщений. Без БД и сети: на входе строки, на выходе решения."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

HIGH = "high"
LEVEL_LABEL = {"high": "высокий", "medium": "средний", "low": "низкий", "unknown": "не определён"}
NBSP = " "


@dataclass(frozen=True, slots=True)
class Thresholds:
    loss_jump_cents: int
    total_loss_jump_pct: float
    cooldown: timedelta


@dataclass(frozen=True, slots=True)
class Alert:
    kind: str  # high_risk | loss_jump | total_loss_jump
    message: str
    customer_id: int | None = None
    email: str | None = None
    risk_level: str | None = None
    prev_level: str | None = None
    expected_loss: int | None = None
    prev_loss: int | None = None

    def link(self, public_url: str) -> str:
        if self.customer_id is None:
            return f"{public_url}/customers?level=high"
        return f"{public_url}/customers/{self.customer_id}"


@dataclass(frozen=True, slots=True)
class StateRow:
    customer_id: int
    risk_level: str
    expected_loss: int
    loss_ref: int
    last_alert_at: datetime | None


@dataclass(slots=True)
class Decision:
    alerts: list[Alert] = field(default_factory=list)
    states: list[StateRow] = field(default_factory=list)
    suppressed: dict[str, int] = field(default_factory=dict)

    def suppress(self, kind: str) -> None:
        self.suppressed[kind] = self.suppressed.get(kind, 0) + 1


@dataclass(frozen=True, slots=True)
class TotalState:
    total_loss: int
    total_loss_ref: int
    total_last_alert_at: datetime | None


def money(cents: int | None) -> str:
    """Евроценты → «€1 234» (от €100 без центов) или «€45,50»."""
    if cents is None:
        return "—"
    sign = "−" if cents < 0 else ""
    cents = abs(int(cents))
    if cents >= 10_000 or cents % 100 == 0:
        body = f"{round(cents / 100):,}".replace(",", NBSP)
    else:
        body = f"{cents // 100},{cents % 100:02d}"
    return f"{sign}€{body}"


def _who(customer_id: int, email: str | None) -> str:
    return f"#{customer_id} ({email})" if email else f"#{customer_id}"


def _cooling(last_alert_at: datetime | None, now: datetime, cooldown: timedelta) -> bool:
    return last_alert_at is not None and now - last_alert_at < cooldown


def customer_message(
    *,
    customer_id: int,
    email: str | None,
    level: str,
    prev_level: str | None,
    loss: int,
    prev_loss: int | None,
    became_high: bool,
) -> str:
    who = _who(customer_id, email)
    if became_high:
        if prev_level is None:
            head = f"Новый клиент {who} сразу в высоком риске"
        else:
            head = f"Клиент {who} перешёл в высокий риск (был {LEVEL_LABEL.get(prev_level, prev_level)})"
        return f"{head}. Ожидаемая потеря {money(loss)}."
    grown = loss - (prev_loss or 0)
    return (
        f"У клиента {who} ожидаемая потеря выросла на {money(grown)}: {money(prev_loss)} → {money(loss)}. "
        f"Риск {LEVEL_LABEL.get(level, level)}."
    )


def evaluate_customers(rows: Iterable[Mapping[str, Any]], thresholds: Thresholds, now: datetime) -> Decision:
    """Сравнивает текущие оценки с тем, что сервис помнит о клиентах.

    Строка: customer_id, email, risk_level, expected_loss и из alerts.customer_state —
    prev_level, prev_loss, loss_ref, last_alert_at (None у нового клиента).
    Одному клиенту за пачку — не больше одного оповещения; в окне анти-спама оповещение
    не создаётся, но состояние обновляется.
    """
    decision = Decision()
    for row in rows:
        cid = int(row["customer_id"])
        level = str(row["risk_level"])
        loss = int(row["expected_loss"])
        prev_level = row.get("prev_level")
        last_alert_at = row.get("last_alert_at")
        is_new = prev_level is None
        loss_ref = loss if is_new else min(int(row["loss_ref"]), loss)

        became_high = level == HIGH and prev_level != HIGH
        loss_jump = not is_new and loss - loss_ref > thresholds.loss_jump_cents
        if not (became_high or loss_jump):
            decision.states.append(StateRow(cid, level, loss, loss_ref, last_alert_at))
            continue

        kind = "high_risk" if became_high else "loss_jump"
        if _cooling(last_alert_at, now, thresholds.cooldown):
            decision.suppress(kind)
            decision.states.append(StateRow(cid, level, loss, loss_ref, last_alert_at))
            continue

        prev_loss = None if is_new else (loss_ref if loss_jump else int(row.get("prev_loss") or 0))
        decision.alerts.append(
            Alert(
                kind=kind,
                customer_id=cid,
                email=row.get("email"),
                risk_level=level,
                prev_level=prev_level,
                expected_loss=loss,
                prev_loss=prev_loss,
                message=customer_message(
                    customer_id=cid,
                    email=row.get("email"),
                    level=level,
                    prev_level=prev_level,
                    loss=loss,
                    prev_loss=prev_loss,
                    became_high=became_high,
                ),
            )
        )
        # после оповещения рост считаем заново от текущего значения
        decision.states.append(StateRow(cid, level, loss, loss, now))
    return decision


def evaluate_total(
    current: int, state: TotalState | None, thresholds: Thresholds, now: datetime
) -> tuple[Alert | None, TotalState, bool]:
    """Общая ожидаемая потеря: рост больше чем на N % от минимума после прошлого оповещения.

    Возвращает (оповещение или None, новое состояние, подавлено ли анти-спамом).
    """
    if state is None:
        return None, TotalState(current, current, None), False
    ref = min(state.total_loss_ref, current)
    grown_pct = (current - ref) / ref * 100 if ref > 0 else 0.0
    if grown_pct <= thresholds.total_loss_jump_pct:
        return None, TotalState(current, ref, state.total_last_alert_at), False
    if _cooling(state.total_last_alert_at, now, thresholds.cooldown):
        return None, TotalState(current, ref, state.total_last_alert_at), True
    pct = f"{grown_pct:.1f}".replace(".", ",")
    alert = Alert(
        kind="total_loss_jump",
        expected_loss=current,
        prev_loss=ref,
        message=f"Общая ожидаемая потеря выросла на {pct}{NBSP}%: {money(ref)} → {money(current)}.",
    )
    return alert, TotalState(current, current, now), False


# --- сообщения для внешних каналов -------------------------------------------------------------


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    if n % 10 == 1:
        return forms[0]
    if 2 <= n % 10 <= 4:
        return forms[1]
    return forms[2]


def batch_text(alerts: Sequence[Alert], public_url: str, max_items: int) -> str:
    """Одно сообщение на пачку: заголовок, до max_items строк со ссылками, хвост «и ещё N»."""
    n = len(alerts)
    lines = [f"ChurnRadar: {n} {_plural(n, ('оповещение', 'оповещения', 'оповещений'))}"]
    # общая потеря и переходы в высокий риск важнее, их — первыми
    order = {"total_loss_jump": 0, "high_risk": 1, "loss_jump": 2}
    shown = sorted(alerts, key=lambda a: order.get(a.kind, 9))[:max_items]
    for alert in shown:
        lines.append(f"• {alert.message} {alert.link(public_url)}")
    rest = n - len(shown)
    if rest > 0:
        lines.append(f"…и ещё {rest}: {public_url}/customers?level=high")
    return "\n".join(lines)


def webhook_payload(
    alerts: Sequence[Alert],
    *,
    batch_id: str,
    event_ids: Sequence[str],
    created_at: datetime,
    public_url: str,
    max_items: int,
) -> dict[str, Any]:
    """Тело webhook. Поле text совместимо со Slack и Mattermost (incoming webhook)."""
    return {
        "type": "churn.alerts",
        "batch_id": batch_id,
        "source_event_ids": list(event_ids),
        "created_at": created_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "text": batch_text(alerts, public_url, max_items),
        "alerts": [
            {
                "kind": a.kind,
                "customer_id": a.customer_id,
                "email": a.email,
                "risk_level": a.risk_level,
                "prev_level": a.prev_level,
                "expected_loss_cents": a.expected_loss,
                "prev_loss_cents": a.prev_loss,
                "message": a.message,
                "url": a.link(public_url),
            }
            for a in alerts
        ],
    }
