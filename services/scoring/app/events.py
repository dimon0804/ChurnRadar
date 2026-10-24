"""Разбор конвертов событий и сведение пачки в один план пересчёта."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SUBJECT_SOURCE = "churn.source.changed"
SUBJECT_SETTINGS = "churn.settings.changed"
SUBJECT_SCORES = "churn.scores.updated"

TYPE_BY_SUBJECT = {
    SUBJECT_SOURCE: "source.changed",
    SUBJECT_SETTINGS: "settings.changed",
}


class InvalidEvent(ValueError):
    """Сообщение нельзя разобрать: его подтверждаем и пропускаем, повтор не поможет."""


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    type: str
    payload: dict[str, Any]

    @property
    def requires_full(self) -> bool:
        if self.type == "settings.changed":
            return True
        if self.type == "source.changed":
            return self.payload.get("kind") != "changes"
        return False

    @property
    def account_ids(self) -> list[int]:
        raw = self.payload.get("account_ids") or []
        return [int(x) for x in raw]


def parse_event(data: bytes, subject: str | None = None) -> Event:
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidEvent(f"не JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise InvalidEvent("конверт должен быть объектом")
    raw_id = doc.get("event_id")
    try:
        event_id = str(uuid.UUID(str(raw_id)))
    except (ValueError, TypeError) as exc:
        raise InvalidEvent(f"некорректный event_id: {raw_id!r}") from exc
    event_type = doc.get("type") or (TYPE_BY_SUBJECT.get(subject or ""))
    if event_type not in {"source.changed", "settings.changed"}:
        raise InvalidEvent(f"неожиданный тип события: {event_type!r}")
    payload = doc.get("payload")
    if not isinstance(payload, dict):
        raise InvalidEvent("payload должен быть объектом")
    if event_type == "source.changed":
        ids = payload.get("account_ids", [])
        if not isinstance(ids, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in ids):
            raise InvalidEvent("account_ids должен быть массивом целых")
    return Event(event_id=event_id, type=event_type, payload=payload)


@dataclass(slots=True)
class Plan:
    """Что нужно сделать по пачке событий: полный пересчёт или по списку клиентов."""

    full: bool = False
    account_ids: set[int] = field(default_factory=set)
    event_ids: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.full and not self.account_ids


def unique_new(events: Iterable[Event], processed: set[str]) -> list[Event]:
    """Отбрасывает уже обработанные события и повторы внутри одной пачки."""
    seen = set(processed)
    fresh: list[Event] = []
    for event in events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        fresh.append(event)
    return fresh


def coalesce(events: Iterable[Event], incremental_max: int) -> Plan:
    plan = Plan()
    for event in events:
        plan.event_ids.append(event.event_id)
        if event.requires_full:
            plan.full = True
        else:
            plan.account_ids.update(event.account_ids)
    if plan.full or len(plan.account_ids) > incremental_max:
        plan.full = True
        plan.account_ids.clear()
    return plan


def make_envelope(event_type: str, payload: dict[str, Any], producer: str = "scoring") -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "type": event_type,
        "occurred_at": iso(datetime.now(UTC)),
        "schema_version": 1,
        "producer": producer,
        "payload": payload,
    }


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def scores_updated_payload(
    *,
    model_version: int,
    scored_at: datetime,
    changed_customer_ids: Iterable[int],
    full: bool,
    duration_ms: float,
    summary: dict[str, Any],
) -> dict[str, Any]:
    by_level = summary.get("by_level") or {}
    return {
        "model_version": model_version,
        "scored_at": iso(scored_at),
        "changed_customer_ids": [] if full else sorted(changed_customer_ids),
        "full": full,
        "duration_ms": round(duration_ms, 1),
        "summary": {
            "customers_total": int(summary.get("customers_total", 0)),
            "by_level": {level: int(by_level.get(level, 0)) for level in ("high", "medium", "low", "unknown")},
            "expected_loss": int(summary.get("expected_loss", 0)),
            "exposure": int(summary.get("exposure", 0)),
        },
    }
