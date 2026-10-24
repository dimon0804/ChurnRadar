"""Интеграция с реальными Postgres и NATS (см. TEST_DATABASE_URL / TEST_NATS_URL в conftest)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import TEST_DATABASE_URL, TEST_NATS_URL, load_snapshot

from app.config import Config
from app.engine import Scorer
from app.worker import Worker

pytestmark = pytest.mark.integration

REFERENCE = datetime(2026, 10, 19, 15, tzinfo=UTC)
LOCK_KEY = 991


async def _count(pool: Any, sql: str, *args: Any) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(sql, *args)


async def test_full_rescore_creates_settings_scores_and_summary(db_pool, sample_data) -> None:  # type: ignore[no-untyped-def]
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    scorer = Scorer(db_pool, lock_key=LOCK_KEY)
    result = await scorer.run(full=True, event_ids=[str(uuid.uuid4())])
    assert result is not None and result.full
    assert await _count(db_pool, "SELECT count(*) FROM scoring.customer_scores") == 10
    assert await _count(db_pool, "SELECT version FROM scoring.settings") == 1
    assert await _count(db_pool, "SELECT count(*) FROM scoring.settings_history") == 1
    assert await _count(db_pool, "SELECT count(*) FROM scoring.processed_events") == 1
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT data, model_version FROM scoring.summary")
    summary = json.loads(row["data"])
    assert summary["customers_total"] == 10
    assert sum(summary["by_level"].values()) == 10
    assert summary["model_version"] == 1
    assert {"timeline", "top_reasons", "money_explanation"} <= summary.keys()


async def test_settings_from_db_are_used(db_pool, sample_data) -> None:  # type: ignore[no-untyped-def]
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
        await conn.execute(
            "INSERT INTO scoring.settings (id, version, data) VALUES (1, 7, $1::jsonb)",
            json.dumps({"horizon_days": 14, "thresholds": {"medium": 10, "high": 20}, "weights": {}}),
        )
    result = await Scorer(db_pool, lock_key=LOCK_KEY).run(full=True)
    assert result is not None
    assert result.model_version == 7
    assert result.summary["horizon_days"] == 14
    assert await _count(db_pool, "SELECT count(DISTINCT model_version) FROM scoring.customer_scores") == 1
    assert await _count(db_pool, "SELECT max(model_version) FROM scoring.customer_scores") == 7


async def test_incremental_updates_and_removes(db_pool, sample_data) -> None:  # type: ignore[no-untyped-def]
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    scorer = Scorer(db_pool, lock_key=LOCK_KEY)
    await scorer.run(full=True)
    async with db_pool.acquire() as conn:
        before = await conn.fetchrow("SELECT balance, scored_at FROM scoring.customer_scores WHERE customer_id = 1")
        untouched = await conn.fetchval("SELECT scored_at FROM scoring.customer_scores WHERE customer_id = 2")
        await conn.execute("UPDATE source.accounts SET balance = balance + 777 WHERE id = 1")
        await conn.execute("DELETE FROM source.accounts WHERE id = 3")

    result = await scorer.run(full=False, account_ids=[1, 3])
    assert result is not None and not result.full
    assert result.changed_ids == [1, 3]
    assert result.removed_ids == [3]
    async with db_pool.acquire() as conn:
        after = await conn.fetchrow("SELECT balance, scored_at FROM scoring.customer_scores WHERE customer_id = 1")
        assert await conn.fetchval("SELECT count(*) FROM scoring.customer_scores WHERE customer_id = 3") == 0
        assert await conn.fetchval("SELECT scored_at FROM scoring.customer_scores WHERE customer_id = 2") == untouched
        summary = json.loads(await conn.fetchval("SELECT data FROM scoring.summary"))
    assert after["balance"] == before["balance"] + 777
    assert summary["customers_total"] == 9


async def test_stale_cache_falls_back_to_full(db_pool, sample_data) -> None:  # type: ignore[no-untyped-def]
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    replica_a = Scorer(db_pool, lock_key=LOCK_KEY)
    replica_b = Scorer(db_pool, lock_key=LOCK_KEY)
    await replica_a.run(full=True)
    await replica_b.run(full=True)
    result = await replica_a.run(full=False, account_ids=[1])
    assert result is not None and result.full


async def test_advisory_lock_blocks_parallel_full(db_pool, sample_data) -> None:  # type: ignore[no-untyped-def]
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    scorer = Scorer(db_pool, lock_key=LOCK_KEY)
    async with db_pool.acquire() as holder:
        await holder.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        try:
            assert await scorer.run(full=True, wait_lock=False) is None
            waiting = asyncio.create_task(scorer.run(full=True))
            await asyncio.sleep(0.5)
            assert not waiting.done()
        finally:
            await holder.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        result = await asyncio.wait_for(waiting, timeout=30)
    assert result is not None and result.full


# --- NATS -----------------------------------------------------------------------------------


@pytest.fixture
async def js():  # type: ignore[no-untyped-def]
    nats = pytest.importorskip("nats")
    try:
        nc = await asyncio.wait_for(nats.connect(TEST_NATS_URL, connect_timeout=2, max_reconnect_attempts=0), 5)
    except Exception as exc:
        pytest.skip(f"NATS недоступен: {exc}")
    ctx = nc.jetstream()
    try:
        await ctx.delete_stream("CHURN")
    except Exception:
        pass
    yield nc, ctx
    await nc.close()


def _config(**overrides: Any) -> Config:
    base = Config.from_env()
    values = {
        **{f: getattr(base, f) for f in base.__dataclass_fields__},
        "database_url": TEST_DATABASE_URL,
        "nats_url": TEST_NATS_URL,
        "debounce_ms": 200,
        "rescore_interval_s": 0,
        "advisory_lock_key": LOCK_KEY,
    }
    values.update(overrides)
    return Config(**values)


def _envelope(event_type: str, payload: dict[str, Any], event_id: str | None = None) -> bytes:
    return json.dumps(
        {
            "event_id": event_id or str(uuid.uuid4()),
            "type": event_type,
            "occurred_at": "2026-10-19T15:00:00.000Z",
            "schema_version": 1,
            "producer": "test",
            "payload": payload,
        }
    ).encode()


async def _next(queue: asyncio.Queue, timeout: float = 20) -> dict[str, Any]:
    return await asyncio.wait_for(queue.get(), timeout)


async def test_worker_consumes_coalesces_and_publishes(db_pool, sample_data, js) -> None:  # type: ignore[no-untyped-def]
    nc, ctx = js
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)

    config = _config()
    worker = Worker(config, db_pool, Scorer(db_pool, lock_key=LOCK_KEY))
    await worker.connect()
    updates: asyncio.Queue = asyncio.Queue()

    async def on_update(msg) -> None:  # type: ignore[no-untyped-def]
        await updates.put(json.loads(msg.data))

    await nc.subscribe("churn.scores.updated", cb=on_update)
    await worker.initial()
    first = await _next(updates)
    assert first["type"] == "scores.updated" and first["payload"]["full"] is True

    consumer = asyncio.create_task(worker.consume())
    try:
        # Пачка из трёх изменений и повтор одного event_id → один инкрементальный пересчёт.
        dup = str(uuid.uuid4())
        for ids, eid in (([1], dup), ([2], None), ([1], dup)):
            await ctx.publish(
                "churn.source.changed",
                _envelope("source.changed", {"kind": "changes", "counts": {}, "account_ids": ids}, eid),
            )
        update = await _next(updates)
        payload = update["payload"]
        assert payload["full"] is False
        assert payload["changed_customer_ids"] == [1, 2]
        assert payload["summary"]["customers_total"] == 10
        assert await _count(db_pool, "SELECT count(*) FROM scoring.processed_events") == 2

        # Повторная доставка уже обработанного события не вызывает пересчёт.
        await ctx.publish(
            "churn.source.changed",
            _envelope("source.changed", {"kind": "changes", "counts": {}, "account_ids": [1]}, dup),
        )
        with pytest.raises(TimeoutError):
            await _next(updates, timeout=2)

        # Смена настроек → полный пересчёт с новой версией.
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE scoring.settings SET version = 2")
        await ctx.publish("churn.settings.changed", _envelope("settings.changed", {"version": 2, "updated_by": "t"}))
        update = await _next(updates)
        assert update["payload"]["full"] is True
        assert update["payload"]["model_version"] == 2
        assert await _count(db_pool, "SELECT count(*) FROM scoring.customer_scores WHERE model_version = 2") == 10

        info = await ctx.consumer_info("CHURN", "scoring")
        assert info.num_ack_pending == 0
    finally:
        worker.stop()
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        await worker.close()


async def test_periodic_rescore_only_without_reference_time(db_pool, sample_data, js) -> None:  # type: ignore[no-untyped-def]
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    worker = Worker(_config(rescore_interval_s=0.5), db_pool, Scorer(db_pool, lock_key=LOCK_KEY))
    await worker.connect()
    await worker.initial()
    stamp = await _count(db_pool, "SELECT extract(epoch FROM scored_at)::float8 FROM scoring.summary")
    periodic = asyncio.create_task(worker.periodic())
    try:
        await asyncio.sleep(1.5)
        assert await _count(db_pool, "SELECT extract(epoch FROM scored_at)::float8 FROM scoring.summary") == stamp
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE source.clock SET reference_time = NULL")
        await asyncio.sleep(1.5)
        assert await _count(db_pool, "SELECT extract(epoch FROM scored_at)::float8 FROM scoring.summary") > stamp
    finally:
        worker.stop()
        await asyncio.gather(periodic, return_exceptions=True)
        await worker.close()


async def test_chunked_full_equals_single_chunk(db_pool, sample_data) -> None:  # type: ignore[no-untyped-def]
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    query = "SELECT customer_id, risk_score, risk_level, expected_loss, reasons FROM scoring.customer_scores ORDER BY 1"
    whole = await Scorer(db_pool, lock_key=LOCK_KEY, chunk_size=10_000).run(full=True)
    async with db_pool.acquire() as conn:
        rows_whole = [tuple(r) for r in await conn.fetch(query)]
    assert whole is not None
    for workers in (1, 2):
        scorer = Scorer(db_pool, lock_key=LOCK_KEY, chunk_size=3, workers=workers)
        try:
            parts = await scorer.run(full=True)
        finally:
            scorer.close()
        async with db_pool.acquire() as conn:
            rows_parts = [tuple(r) for r in await conn.fetch(query)]
        assert rows_parts == rows_whole, workers
        assert parts is not None
        for key in ("by_level", "expected_loss", "exposure", "top_reasons", "timeline"):
            assert parts.summary[key] == whole.summary[key], (workers, key)
