"""Интеграция с реальными Postgres и NATS (см. TEST_DATABASE_URL / TEST_NATS_URL в conftest)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from svckit.bus import ensure_stream

from app.config import Config
from app.engine import LockTimeout, Scorer
from app.worker import Worker, ensure_consumer
from conftest import TEST_DATABASE_URL, TEST_NATS_URL, load_snapshot

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
    assert {"timeline", "top_reasons", "money_explanation", "risk_scores"} <= summary.keys()
    # агрегаты score лежат в сводке, чтобы /metrics не перебирал клиентов
    async with db_pool.acquire() as conn:
        scores = [r["risk_score"] for r in await conn.fetch("SELECT risk_score FROM scoring.customer_scores")]
    scored = [s for s in scores if s is not None]
    assert summary["risk_scores"]["scored"] == len(scored)
    assert summary["risk_scores"]["avg"] == pytest.approx(sum(scored) / len(scored))
    assert summary["risk_scores"]["max"] == pytest.approx(max(scored))
    # маркетинговые метрики считаются тем же пересчётом и лежат в той же строке
    marketing = summary["marketing"]
    assert marketing["mrr"] == summary["monthly_revenue_total"]
    assert marketing["customers_total"] == 10
    assert marketing["ltv_fact"]["total"] == 32_487  # проведённые транзакции кейса, посчитаны руками
    assert marketing["churn"]["fact"]["customers_left"] == 4
    async with db_pool.acquire() as conn:
        detail = json.loads(await conn.fetchval("SELECT detail FROM scoring.customer_scores WHERE customer_id = 1"))
    assert detail["marketing"]["ltv_fact"] == 3_340


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
    assert summary["marketing"]["customers_total"] == 9
    assert summary["marketing"]["mrr"] == summary["monthly_revenue_total"]


async def test_summary_history_points(db_pool, sample_data) -> None:  # type: ignore[no-untyped-def]
    """Полный пересчёт пишет точку истории всегда, частичный сразу после него — нет (пауза)."""
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    scorer = Scorer(db_pool, lock_key=LOCK_KEY)
    await scorer.run(full=True)
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE source.accounts SET balance = balance + 1 WHERE id = 1")
    await scorer.run(full=False, account_ids=[1])
    await scorer.run(full=True)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM scoring.summary_history ORDER BY id")
        summary = json.loads(await conn.fetchval("SELECT data FROM scoring.summary"))
        # колонки фильтра по причине Postgres считает сам из reasons и detail
        codes = await conn.fetchrow(
            "SELECT main_reason, reason_codes FROM scoring.customer_scores WHERE main_reason IS NOT NULL LIMIT 1"
        )
    assert [r["full_run"] for r in rows] == [True, True]
    last = rows[-1]
    assert last["customers_total"] == summary["customers_total"] == 10
    assert last["expected_loss"] == summary["expected_loss"]
    assert last["high"] == summary["by_level"]["high"]
    assert codes is not None and codes["main_reason"] in json.loads(codes["reason_codes"])


async def test_summary_history_is_thinned(db_pool) -> None:  # type: ignore[no-untyped-def]
    from app import repository as repo

    summary = {"customers_total": 1, "by_level": {"high": 1}, "expected_loss": 5}
    async with db_pool.acquire() as conn:
        for i in range(12):
            await repo.record_history(conn, summary, 1, REFERENCE.replace(minute=i), full=True, limit=10)
        rows = await conn.fetch("SELECT scored_at FROM scoring.summary_history ORDER BY scored_at")
        # частичный пересчёт в пределах паузы точку не пишет
        assert not await repo.record_history(conn, summary, 1, REFERENCE.replace(minute=11, second=5), full=False)
    minutes = [r["scored_at"].minute for r in rows]
    assert len(minutes) <= 10
    assert minutes[0] == 0 and minutes[-1] == 11  # первая и свежая точки на месте
    assert minutes == sorted(minutes)


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
    with contextlib.suppress(Exception):
        await ctx.delete_stream("CHURN")
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
        for key in ("by_level", "expected_loss", "exposure", "top_reasons", "timeline", "risk_scores", "marketing"):
            assert parts.summary[key] == whole.summary[key], (workers, key)


# --- живучесть ------------------------------------------------------------------------------


async def test_lock_timeout_and_no_leak_after_cancel(db_pool, sample_data) -> None:  # type: ignore[no-untyped-def]
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    async with db_pool.acquire() as holder:
        await holder.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        try:
            with pytest.raises(LockTimeout):
                await Scorer(db_pool, lock_key=LOCK_KEY, lock_timeout_s=0.3).run(full=True)
            # lock_timeout_s=0 — ждать без ограничения; отменяем посреди ожидания
            waiting = asyncio.create_task(Scorer(db_pool, lock_key=LOCK_KEY, lock_timeout_s=0).run(full=True))
            await asyncio.sleep(0.3)
            assert not waiting.done()
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        finally:
            await holder.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
    # ни таймаут, ни отмена не оставили lock на соединениях пула
    assert await Scorer(db_pool, lock_key=LOCK_KEY).run(full=True, wait_lock=False) is not None
    assert await _count(db_pool, "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'") == 0


async def test_consumer_config_updated_on_drift(js) -> None:  # type: ignore[no-untyped-def]
    _, ctx = js
    await ensure_stream(ctx, "CHURN")
    await ctx.add_consumer(
        "CHURN",
        config=ConsumerConfig(
            name="scoring",
            durable_name="scoring",
            ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.ALL,
            ack_wait=30,
            max_ack_pending=10_000,
            filter_subjects=["churn.source.changed", "churn.settings.changed"],
        ),
    )
    await ensure_consumer(ctx, "CHURN", "scoring", ack_wait_s=300, max_deliver=10)
    info = await ctx.consumer_info("CHURN", "scoring")
    assert info.config.max_deliver == 11
    assert info.config.ack_wait == pytest.approx(300)
    assert not info.config.backoff


async def test_server_backoff_overrides_ack_wait(js) -> None:  # type: ignore[no-untyped-def]
    """Почему backoff делаем через nak(delay), а не в конфиге потребителя."""
    _, ctx = js
    await ensure_stream(ctx, "CHURN")
    await ctx.add_consumer(
        "CHURN",
        config=ConsumerConfig(
            name="backoff-probe",
            durable_name="backoff-probe",
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=300,
            max_deliver=10,
            backoff=[1.0, 5.0, 30.0, 120.0],
            filter_subjects=["churn.source.changed"],
        ),
    )
    info = await ctx.consumer_info("CHURN", "backoff-probe")
    assert info.config.ack_wait == pytest.approx(1.0)


async def test_poison_event_lands_in_dlq(db_pool, sample_data, js) -> None:  # type: ignore[no-untyped-def]
    nc, ctx = js
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    scorer = Scorer(db_pool, lock_key=LOCK_KEY)

    async def broken(**kwargs: Any) -> None:
        raise RuntimeError("poison")

    scorer.run = broken  # type: ignore[method-assign]
    worker = Worker(_config(max_deliver=3, retry_backoff_s=(0.1,), debounce_ms=0), db_pool, scorer)
    await worker.connect()
    letters: asyncio.Queue = asyncio.Queue()

    async def on_letter(msg) -> None:  # type: ignore[no-untyped-def]
        await letters.put(msg)

    await nc.subscribe("churn.dlq.scoring", cb=on_letter)
    consumer = asyncio.create_task(worker.consume())
    try:
        body = _envelope("source.changed", {"kind": "changes", "counts": {}, "account_ids": [1]})
        ack = await ctx.publish("churn.source.changed", body)
        letter = await asyncio.wait_for(letters.get(), 20)
        assert letter.data == body
        assert letter.headers["Churn-Original-Subject"] == "churn.source.changed"
        assert letter.headers["Churn-Deliveries"] == "3"
        assert letter.headers["Churn-Stream-Seq"] == str(ack.seq)
        assert "poison" in letter.headers["Churn-Error"]
        # копия лежит в стриме CHURN (churn.> покрывает DLQ), исходное больше не доставляется
        stored = await ctx.get_last_msg("CHURN", "churn.dlq.scoring")
        assert stored.data == body
        await asyncio.sleep(0.5)
        info = await ctx.consumer_info("CHURN", "scoring")
        assert info.num_ack_pending == 0 and info.num_pending == 0
        assert letters.empty()
    finally:
        worker.stop()
        await asyncio.gather(consumer, return_exceptions=True)
        await worker.close()


async def test_cancel_mid_rescore_returns_messages_at_once(db_pool, sample_data, js) -> None:  # type: ignore[no-untyped-def]
    _, ctx = js
    scorer = Scorer(db_pool, lock_key=LOCK_KEY)
    started = asyncio.Event()

    async def slow(**kwargs: Any) -> None:
        started.set()
        await asyncio.sleep(3600)

    scorer.run = slow  # type: ignore[method-assign]
    worker = Worker(_config(debounce_ms=0), db_pool, scorer)
    await worker.connect()
    consumer = asyncio.create_task(worker.consume())
    try:
        await ctx.publish(
            "churn.source.changed",
            _envelope("source.changed", {"kind": "changes", "counts": {}, "account_ids": [1]}),
        )
        await asyncio.wait_for(started.wait(), 20)
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        assert not worker.subscribed
        await worker.close()
        # «другая реплика» получает сообщение через секунды; без nak ждала бы ack_wait (300 с)
        other = await ctx.pull_subscribe_bind("scoring", stream="CHURN")
        loop = asyncio.get_running_loop()
        began = loop.time()
        msgs = await other.fetch(1, timeout=15)
        assert len(msgs) == 1 and json.loads(msgs[0].data)["payload"]["account_ids"] == [1]
        assert msgs[0].metadata.num_delivered == 2
        assert loop.time() - began < 10
        await msgs[0].ack()
    finally:
        worker.stop()
        await worker.close()


# --- гонка с заменой снимка в ingest --------------------------------------------------------


async def _wait_blocked(pool: Any, timeout: float = 10) -> None:
    """Ждём, пока кто-то встанет в очередь за блокировкой таблицы source.*."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        waiting = await _count(
            pool,
            "SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
            "WHERE NOT l.granted AND c.relnamespace = 'source'::regnamespace",
        )
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("никто не ждёт блокировку source.*")


@contextlib.asynccontextmanager
async def _replacing_snapshot(data: dict[str, Any], *, truncate_history: bool):  # type: ignore[no-untyped-def]
    """Как replace_snapshot в ingest: TRUNCATE и COPY в открытой транзакции, коммит — на выходе."""
    import asyncpg

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    tx = conn.transaction()
    await tx.start()
    try:
        await load_snapshot(conn, data, REFERENCE, truncate_history=truncate_history)
        yield
        await tx.commit()
    except BaseException:
        await tx.rollback()
        raise
    finally:
        await conn.close()


@pytest.mark.parametrize("truncate_history", [False, True])
async def test_full_rescore_during_snapshot_replace_sees_new_data(  # type: ignore[no-untyped-def]
    db_pool, sample_data, truncate_history
) -> None:
    """TRUNCATE не MVCC-безопасен: снимок REPEATABLE READ, взятый до коммита ingest, видит таблицы пустыми.
    Пересчёт, начатый во время замены, должен дождаться её и посчитать новый снимок, а не 0 клиентов."""
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    scorer = Scorer(db_pool, lock_key=LOCK_KEY)
    await scorer.run(full=True)

    async with _replacing_snapshot(sample_data, truncate_history=truncate_history):
        running = asyncio.create_task(scorer.run(full=True))
        await _wait_blocked(db_pool)
    result = await asyncio.wait_for(running, timeout=30)

    assert result is not None
    assert result.customers == 10
    assert result.summary["customers_total"] == 10
    assert await _count(db_pool, "SELECT count(*) FROM scoring.customer_scores") == 10


async def test_preview_during_snapshot_replace_sees_new_data(db_pool, sample_data) -> None:  # type: ignore[no-untyped-def]
    import riskcore

    from app.preview import Previewer

    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    await Scorer(db_pool, lock_key=LOCK_KEY).run(full=True)
    proposed = riskcore.default_settings().to_storage()

    async with _replacing_snapshot(sample_data, truncate_history=False):
        running = asyncio.create_task(Previewer(db_pool).preview(proposed))
        await _wait_blocked(db_pool)
    result = await asyncio.wait_for(running, timeout=30)

    assert result["customers_total"] == 10
    assert result["sample_size"] == 10
