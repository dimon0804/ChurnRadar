"""BatchProcessor без NATS и Postgres: коалесинг, идемпотентность, подтверждения."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app import repository as repo
from app.engine import RunResult
from app.worker import BatchProcessor

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts" / "events"


class FakeMsg:
    def __init__(self, body: bytes, subject: str = "churn.source.changed") -> None:
        self.data = body
        self.subject = subject
        self.acked = self.naked = self.termed = False

    async def ack(self) -> None:
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        self.naked = True

    async def term(self) -> None:
        self.termed = True

    async def in_progress(self) -> None:
        pass


class FakeConn:
    def __init__(self, processed: set[str]) -> None:
        self.processed = processed

    async def fetch(self, sql: str, ids: list[str]) -> list[tuple[str]]:
        return [(i,) for i in ids if i in self.processed]

    async def execute(self, sql: str, ids: list[str]) -> None:
        self.processed.update(ids)


class FakePool:
    def __init__(self) -> None:
        self.processed: set[str] = set()

    @asynccontextmanager
    async def acquire(self):  # type: ignore[no-untyped-def]
        yield FakeConn(self.processed)


class FakeScorer:
    def __init__(self, pool: FakePool, fail: bool = False) -> None:
        self.pool = pool
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def run(self, *, full: bool, account_ids: list[int], event_ids: list[str]) -> RunResult:
        self.calls.append({"full": full, "account_ids": account_ids, "event_ids": event_ids})
        if self.fail:
            raise RuntimeError("boom")
        self.pool.processed.update(event_ids)  # в реальности — в той же транзакции, что и результаты
        return RunResult(
            full=full,
            changed_ids=account_ids,
            removed_ids=[],
            summary={"customers_total": 1, "by_level": {"high": 1}, "expected_loss": 5, "exposure": 7},
            model_version=1,
            scored_at=datetime(2026, 10, 19, 15, tzinfo=UTC),
            duration_ms=3.0,
            customers=1,
        )


def _body(kind: str, ids: list[int], event_id: str | None = None) -> bytes:
    return json.dumps(
        {
            "event_id": event_id or str(uuid.uuid4()),
            "type": "source.changed",
            "occurred_at": "2026-10-19T15:00:00.000Z",
            "schema_version": 1,
            "payload": {"kind": kind, "counts": {}, "account_ids": ids},
        }
    ).encode()


@pytest.fixture
def setup() -> tuple[FakePool, FakeScorer, list[RunResult], BatchProcessor]:
    pool = FakePool()
    scorer = FakeScorer(pool)
    published: list[RunResult] = []

    async def publish(result: RunResult) -> None:
        published.append(result)

    processor = BatchProcessor(pool, scorer, publish, incremental_max=100)  # type: ignore[arg-type]
    return pool, scorer, published, processor


async def test_batch_coalesced_into_one_run(setup) -> None:  # type: ignore[no-untyped-def]
    _, scorer, published, processor = setup
    msgs = [FakeMsg(_body("changes", [i, i + 1])) for i in range(1, 6)]
    await processor.handle(msgs)
    assert len(scorer.calls) == 1
    assert scorer.calls[0]["full"] is False
    assert scorer.calls[0]["account_ids"] == [1, 2, 3, 4, 5, 6]
    assert len(scorer.calls[0]["event_ids"]) == 5
    assert all(m.acked for m in msgs)
    assert len(published) == 1


async def test_redelivered_event_not_rescored(setup) -> None:  # type: ignore[no-untyped-def]
    _, scorer, published, processor = setup
    event_id = str(uuid.uuid4())
    await processor.handle([FakeMsg(_body("changes", [1], event_id))])
    again = FakeMsg(_body("changes", [1], event_id))
    await processor.handle([again])
    assert len(scorer.calls) == 1
    assert again.acked
    assert len(published) == 1


async def test_invalid_message_terminated(setup) -> None:  # type: ignore[no-untyped-def]
    _, scorer, _, processor = setup
    bad = FakeMsg(b"{oops")
    good = FakeMsg(_body("snapshot", []))
    await processor.handle([bad, good])
    assert bad.termed and not bad.acked
    assert good.acked
    assert scorer.calls[0]["full"] is True


async def test_failure_naks_and_keeps_events_unprocessed() -> None:
    pool = FakePool()
    scorer = FakeScorer(pool, fail=True)

    async def publish(result: RunResult) -> None:  # pragma: no cover
        raise AssertionError

    processor = BatchProcessor(pool, scorer, publish, incremental_max=100)  # type: ignore[arg-type]
    msg = FakeMsg(_body("changes", [1]))
    await processor.handle([msg])
    assert msg.naked and not msg.acked
    assert pool.processed == set()


async def test_empty_changes_marked_processed(setup) -> None:  # type: ignore[no-untyped-def]
    pool, scorer, _, processor = setup
    msg = FakeMsg(_body("changes", []))
    await processor.handle([msg])
    assert scorer.calls == []
    assert msg.acked
    assert len(pool.processed) == 1


def test_score_record_order_and_json() -> None:
    scored_at = datetime(2026, 10, 19, 15, tzinfo=UTC)
    row = {
        "customer_id": 7,
        "email": "a@b",
        "balance": 1,
        "bonus_balance": 2,
        "bonus_state": "locked",
        "withdraw_balance": 0,
        "total_replenished": 3,
        "risk_score": None,
        "risk_level": "unknown",
        "loyalty": None,
        "reasons": [{"code": "x", "title": "т", "contribution": 0}],
        "services_total": 0,
        "services_at_risk": 0,
        "monthly_revenue": 0,
        "expected_loss": 0,
        "exposure": 0,
        "detail": {"when": scored_at},
        "model_version": 99,
        "scored_at": None,
    }
    record = repo.score_record(row, 3, scored_at)
    assert len(record) == len(repo.SCORE_COLUMNS)
    values = dict(zip(repo.SCORE_COLUMNS, record, strict=True))
    assert values["customer_id"] == 7
    assert values["model_version"] == 3
    assert values["scored_at"] == scored_at
    assert json.loads(values["reasons"])[0]["title"] == "т"
    assert json.loads(values["detail"]) == {"when": "2026-10-19T15:00:00.000Z"}


def test_published_payload_matches_contract() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    from app.events import make_envelope, scores_updated_payload

    payload = scores_updated_payload(
        model_version=1,
        scored_at=datetime(2026, 10, 19, 15, tzinfo=UTC),
        changed_customer_ids=[1],
        full=False,
        duration_ms=1.0,
        summary={"customers_total": 1, "by_level": {}, "expected_loss": 0, "exposure": 0},
    )
    schema = json.loads((CONTRACTS / "scores.updated.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)
    envelope_schema = json.loads((CONTRACTS / "envelope.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(make_envelope("scores.updated", payload), envelope_schema)
