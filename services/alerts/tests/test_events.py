"""Разбор churn.scores.updated и коалесинг пачки."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.events import InvalidEvent, coalesce, parse_event, unique_new

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "events" / "scores.updated.schema.json"


def envelope(payload: dict, event_id: str | None = None) -> bytes:
    return json.dumps(
        {
            "event_id": event_id or str(uuid.uuid4()),
            "type": "scores.updated",
            "occurred_at": "2026-10-19T15:00:00.000Z",
            "schema_version": 1,
            "producer": "scoring",
            "payload": payload,
        }
    ).encode()


def payload(ids: list[int], *, full: bool = False, loss: int = 1000, at: str = "2026-10-19T15:00:00.000Z") -> dict:
    # та же форма, что собирает scoring (services/scoring/app/events.py scores_updated_payload)
    return {
        "model_version": 1,
        "scored_at": at,
        "changed_customer_ids": ids,
        "full": full,
        "duration_ms": 12.3,
        "summary": {"customers_total": 10, "by_level": {"high": 1}, "expected_loss": loss, "exposure": 5000},
    }


def test_payload_matches_contract_fields() -> None:
    schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert set(schema["required"]) <= payload([1]).keys()


def test_parse_incremental_and_full() -> None:
    event = parse_event(envelope(payload([3, 1])))
    assert not event.full and event.changed_ids == (3, 1) and event.total_loss == 1000
    assert parse_event(envelope(payload([], full=True))).full
    # пустой список без флага full — проверяем всех, так надёжнее
    assert parse_event(envelope(payload([]))).full


def test_parse_without_summary() -> None:
    body = payload([1])
    del body["summary"]
    assert parse_event(envelope(body)).total_loss is None


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        json.dumps({"event_id": "nope", "payload": {}}).encode(),
        json.dumps({"event_id": str(uuid.uuid4()), "type": "source.changed", "payload": {}}).encode(),
        json.dumps({"event_id": str(uuid.uuid4()), "payload": {"changed_customer_ids": ["x"]}}).encode(),
        json.dumps({"event_id": str(uuid.uuid4()), "payload": []}).encode(),
    ],
)
def test_invalid(raw: bytes) -> None:
    with pytest.raises(InvalidEvent):
        parse_event(raw)


def test_unique_and_coalesce() -> None:
    a = parse_event(envelope(payload([1, 2], loss=100, at="2026-10-19T15:00:00.000Z")))
    b = parse_event(envelope(payload([2, 3], loss=300, at="2026-10-19T15:00:02.000Z")))
    c = parse_event(envelope(payload([9], loss=200, at="2026-10-19T15:00:01.000Z")))
    fresh = unique_new([a, b, a, c], {c.event_id})
    assert [e.event_id for e in fresh] == [a.event_id, b.event_id]
    plan = coalesce(fresh)
    assert not plan.full and plan.customer_ids == {1, 2, 3}
    assert plan.total_loss == 300  # из самого свежего пересчёта

    full = parse_event(envelope(payload([], full=True)))
    plan = coalesce([a, full])
    assert plan.full and plan.customer_ids == set() and len(plan.event_ids) == 2
