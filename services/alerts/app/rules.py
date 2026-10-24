"""Общие мелочи текстов: деньги, склонения, пути в панели и журнал для общего канала команды."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

NBSP = " "

# пути в панели для записей без клиента
HIGH_RISK_PATH = "/customers?level=high"
BULK_PATH = "/customers?level=high&sort=expected_loss&order=desc"


def feed_path(kind: str, customer_id: int | None) -> str:
    """Путь в панели для записи журнала: карточка клиента или отфильтрованный список."""
    if customer_id is not None:
        return f"/customers/{customer_id}"
    if kind in ("bulk", "big_customer_high"):
        return BULK_PATH
    if kind in ("loss_growth", "risk_share", "total_loss_jump"):
        return "/"
    if kind == "stale_data":
        return "/data"
    return HIGH_RISK_PATH


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


def plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    if n % 10 == 1:
        return forms[0]
    if 2 <= n % 10 <= 4:
        return forms[1]
    return forms[2]


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """Запись общего журнала (alerts.feed): событие по порогам системы, а не конкретного пользователя."""

    kind: str
    message: str
    customer_id: int | None = None
    expected_loss: int | None = None
    prev_loss: int | None = None

    def link(self, public_url: str) -> str:
        return public_url + feed_path(self.kind, self.customer_id)


def team_text(entries: Sequence[JournalEntry], public_url: str) -> str:
    """Сообщение в общий чат команды (TELEGRAM_CHAT_ID): по строке на событие со ссылкой."""
    n = len(entries)
    lines = [f"ChurnRadar: {n} {plural(n, ('событие', 'события', 'событий'))}"]
    lines += [f"• {e.message} {e.link(public_url)}" for e in entries]
    return "\n".join(lines)


def webhook_payload(
    entries: Sequence[JournalEntry], *, batch_id: str, event_ids: Sequence[str], created_at: datetime, public_url: str
) -> dict[str, Any]:
    """Тело webhook. Поле text совместимо со Slack и Mattermost (incoming webhook)."""
    return {
        "type": "churn.alerts",
        "batch_id": batch_id,
        "source_event_ids": list(event_ids),
        "created_at": created_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "text": team_text(entries, public_url),
        "alerts": [
            {
                "kind": e.kind,
                "customer_id": e.customer_id,
                "expected_loss_cents": e.expected_loss,
                "prev_loss_cents": e.prev_loss,
                "message": e.message,
                "url": e.link(public_url),
            }
            for e in entries
        ],
    }
