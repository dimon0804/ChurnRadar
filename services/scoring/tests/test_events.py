from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from app.events import (
    Event,
    InvalidEvent,
    coalesce,
    make_envelope,
    parse_event,
    scores_updated_payload,
    unique_new,
)


def _source(kind: str, ids: list[int] | None = None, event_id: str | None = None) -> Event:
    return Event(
        event_id=event_id or str(uuid.uuid4()),
        type="source.changed",
        payload={"kind": kind, "counts": {}, "account_ids": ids or []},
    )


def _settings(event_id: str | None = None) -> Event:
    return Event(event_id=event_id or str(uuid.uuid4()), type="settings.changed", payload={"version": 2})


def _raw(event_type: str, payload: dict, event_id: str | None = None) -> bytes:
    return json.dumps(
        {
            "event_id": event_id or str(uuid.uuid4()),
            "type": event_type,
            "occurred_at": "2026-10-19T15:00:00.000Z",
            "schema_version": 1,
            "producer": "ingest",
            "payload": payload,
        }
    ).encode()


class TestParse:
    def test_source_changes(self) -> None:
        event = parse_event(_raw("source.changed", {"kind": "changes", "counts": {}, "account_ids": [3, 1]}))
        assert event.type == "source.changed"
        assert event.account_ids == [3, 1]
        assert not event.requires_full

    def test_snapshot_requires_full(self) -> None:
        event = parse_event(_raw("source.changed", {"kind": "snapshot", "counts": {}, "account_ids": []}))
        assert event.requires_full

    def test_settings_requires_full(self) -> None:
        event = parse_event(_raw("settings.changed", {"version": 3, "updated_by": "admin"}))
        assert event.requires_full

    def test_type_falls_back_to_subject(self) -> None:
        body = json.dumps({"event_id": str(uuid.uuid4()), "payload": {"version": 2, "updated_by": None}}).encode()
        assert parse_event(body, "churn.settings.changed").type == "settings.changed"

    @pytest.mark.parametrize(
        "body",
        [
            b"not json",
            b"[]",
            json.dumps({"event_id": "nope", "type": "settings.changed", "payload": {}}).encode(),
            json.dumps({"event_id": str(uuid.uuid4()), "type": "scores.updated", "payload": {}}).encode(),
            json.dumps({"event_id": str(uuid.uuid4()), "type": "settings.changed", "payload": []}).encode(),
            _raw("source.changed", {"kind": "changes", "account_ids": ["1"]}),
        ],
    )
    def test_invalid(self, body: bytes) -> None:
        with pytest.raises(InvalidEvent):
            parse_event(body)


class TestIdempotency:
    def test_skips_processed(self) -> None:
        done = _source("changes", [1])
        new = _source("changes", [2])
        assert unique_new([done, new], {done.event_id}) == [new]

    def test_skips_duplicates_inside_batch(self) -> None:
        event_id = str(uuid.uuid4())
        first = _source("changes", [1], event_id)
        again = _source("changes", [1], event_id)
        assert unique_new([first, again], set()) == [first]

    def test_all_processed_gives_empty_plan(self) -> None:
        event = _source("snapshot")
        plan = coalesce(unique_new([event], {event.event_id}), incremental_max=100)
        assert plan.is_empty
        assert plan.event_ids == []


class TestCoalesce:
    def test_union_of_accounts(self) -> None:
        plan = coalesce([_source("changes", [1, 2]), _source("changes", [2, 3])], incremental_max=100)
        assert not plan.full
        assert plan.account_ids == {1, 2, 3}
        assert len(plan.event_ids) == 2

    def test_snapshot_wins(self) -> None:
        plan = coalesce([_source("changes", [1]), _source("snapshot"), _source("changes", [5])], incremental_max=100)
        assert plan.full
        assert plan.account_ids == set()
        assert len(plan.event_ids) == 3

    def test_settings_wins(self) -> None:
        plan = coalesce([_source("changes", [1]), _settings()], incremental_max=100)
        assert plan.full

    def test_too_many_accounts_becomes_full(self) -> None:
        plan = coalesce([_source("changes", list(range(1, 12)))], incremental_max=10)
        assert plan.full
        assert not plan.account_ids

    def test_changes_without_accounts_is_empty(self) -> None:
        plan = coalesce([_source("changes", [])], incremental_max=10)
        assert plan.is_empty
        assert len(plan.event_ids) == 1


def test_scores_updated_payload_shape() -> None:
    summary = {
        "customers_total": 10,
        "by_level": {"high": 4, "medium": 2, "low": 0, "unknown": 4},
        "expected_loss": 123,
        "exposure": 456,
        "timeline": [],
    }
    scored_at = datetime(2026, 10, 19, 15, 0, 0, 123000, tzinfo=UTC)
    payload = scores_updated_payload(
        model_version=2, scored_at=scored_at, changed_customer_ids=[3, 1], full=False, duration_ms=12.345,
        summary=summary,
    )
    assert payload == {
        "model_version": 2,
        "scored_at": "2026-10-19T15:00:00.123Z",
        "changed_customer_ids": [1, 3],
        "full": False,
        "duration_ms": 12.3,
        "summary": {
            "customers_total": 10,
            "by_level": {"high": 4, "medium": 2, "low": 0, "unknown": 4},
            "expected_loss": 123,
            "exposure": 456,
        },
    }
    full = scores_updated_payload(
        model_version=2, scored_at=scored_at, changed_customer_ids=[1], full=True, duration_ms=1, summary=summary
    )
    assert full["changed_customer_ids"] == []


def test_envelope() -> None:
    env = make_envelope("scores.updated", {"x": 1})
    uuid.UUID(env["event_id"])
    assert env["schema_version"] == 1
    assert env["producer"] == "scoring"
    assert env["occurred_at"].endswith("Z")
