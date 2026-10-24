"""Разбор churn.scores.updated и сведение пачки событий в один план проверки."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

SUBJECT_SCORES = "churn.scores.updated"
SUBJECT_DLQ = "churn.dlq.alerts"  # входит в стрим CHURN (churn.>), но не в фильтр потребителя


class InvalidEvent(ValueError):
    """Сообщение нельзя разобрать: подтверждаем и пропускаем, повтор не поможет."""


@dataclass(frozen=True, slots=True)
class ScoresEvent:
    event_id: str
    full: bool
    changed_ids: tuple[int, ...]
    scored_at: str
    # общая ожидаемая потеря из summary события, евроценты; None — в событии её нет
    total_loss: int | None


def parse_event(data: bytes) -> ScoresEvent:
    """Конверт события (contracts/events/envelope.schema.json) с payload scores.updated."""
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
    event_type = doc.get("type") or "scores.updated"
    if event_type != "scores.updated":
        raise InvalidEvent(f"неожиданный тип события: {event_type!r}")
    payload = doc.get("payload")
    if not isinstance(payload, dict):
        raise InvalidEvent("payload должен быть объектом")
    ids = payload.get("changed_customer_ids") or []
    if not isinstance(ids, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in ids):
        raise InvalidEvent("changed_customer_ids должен быть массивом целых")
    full = bool(payload.get("full")) or not ids
    summary = payload.get("summary")
    total = summary.get("expected_loss") if isinstance(summary, dict) else None
    total_loss = int(total) if isinstance(total, int | float) and not isinstance(total, bool) else None
    return ScoresEvent(
        event_id=event_id,
        full=full,
        changed_ids=tuple(ids),
        scored_at=str(payload.get("scored_at") or ""),
        total_loss=total_loss,
    )


@dataclass(slots=True)
class Plan:
    """Что проверить по пачке: всех клиентов или только изменившихся."""

    full: bool = False
    customer_ids: set[int] = field(default_factory=set)
    event_ids: list[str] = field(default_factory=list)
    total_loss: int | None = None


def unique_new(events: Iterable[ScoresEvent], processed: set[str]) -> list[ScoresEvent]:
    """Отбрасывает уже обработанные события и повторы внутри одной пачки."""
    seen = set(processed)
    fresh: list[ScoresEvent] = []
    for event in events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        fresh.append(event)
    return fresh


def coalesce(events: Iterable[ScoresEvent]) -> Plan:
    """Несколько пересчётов подряд — одна проверка. Общая потеря берётся из самого свежего события."""
    plan = Plan()
    latest = ""
    for event in events:
        plan.event_ids.append(event.event_id)
        if event.full:
            plan.full = True
        else:
            plan.customer_ids.update(event.changed_ids)
        if event.total_loss is not None and event.scored_at >= latest:
            latest = event.scored_at
            plan.total_loss = event.total_loss
    if plan.full:
        plan.customer_ids.clear()
    return plan
