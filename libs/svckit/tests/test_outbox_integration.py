"""Relay outbox против настоящих Postgres и NATS JetStream (нужны TEST_DATABASE_URL и TEST_NATS_URL)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os

import asyncpg
import nats
import pytest
from nats.js.errors import NotFoundError
from prometheus_client import CollectorRegistry

from svckit import outbox
from svckit.bus import STREAM_NAME, EventBus
from svckit.db import create_pool

pytestmark = pytest.mark.integration

DB_URL = os.getenv("TEST_DATABASE_URL")
NATS_URL = os.getenv("TEST_NATS_URL")
SCHEMA = "svckit_outbox_test"
TABLE = f"{SCHEMA}.outbox"
SUBJECT = "churn.test.outbox"

DDL = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {TABLE} (
    id bigserial PRIMARY KEY, event_id uuid NOT NULL UNIQUE, subject text NOT NULL, type text NOT NULL,
    payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
    attempts integer NOT NULL DEFAULT 0, last_error text
);
CREATE INDEX outbox_pending_idx ON {TABLE} (id) WHERE published_at IS NULL;
"""


async def drop_schema() -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    finally:
        await conn.close()


async def create_table() -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute(DDL)
    finally:
        await conn.close()


async def stream_messages(subject: str = SUBJECT) -> list[bytes]:
    """Все сообщения стрима по subject (стрим перед тестом чистим)."""
    nc = await nats.connect(NATS_URL)
    try:
        js = nc.jetstream()
        sub = await js.pull_subscribe(subject, stream=STREAM_NAME)
        out: list[bytes] = []
        try:
            while True:
                msgs = await sub.fetch(100, timeout=0.5)
                out.extend(m.data for m in msgs)
                for m in msgs:
                    await m.ack()
        except nats.errors.TimeoutError:
            pass
        await sub.unsubscribe()
        return out
    finally:
        await nc.close()


async def purge_subject() -> None:
    nc = await nats.connect(NATS_URL)
    try:
        with contextlib.suppress(NotFoundError):
            await nc.jetstream().purge_stream(STREAM_NAME, subject=SUBJECT)
    finally:
        await nc.close()


@pytest.fixture
async def env():
    if not DB_URL or not NATS_URL:
        pytest.skip("нужны TEST_DATABASE_URL и TEST_NATS_URL")
    try:
        await drop_schema()
        await purge_subject()
    except (OSError, asyncpg.PostgresError, nats.errors.NoServersError) as exc:
        pytest.skip(f"Postgres/NATS недоступны: {exc}")
    pool = await create_pool(DB_URL, 1, 6, timeout_s=0)
    eb = EventBus(NATS_URL, producer="svckit-test")
    await eb.start(wait_s=10)
    yield pool, eb
    await eb.close()
    await pool.close()
    await drop_schema()


async def add_events(pool, n: int) -> list[str]:
    ids = []
    async with pool.acquire() as conn, conn.transaction():
        for i in range(n):
            ids.append(await outbox.add_event(conn, TABLE, SUBJECT, "test.event", {"n": i}))
    return ids


async def test_two_replicas_publish_each_event_once(env):
    pool, eb = env
    await create_table()
    ids = await add_events(pool, 60)
    relays = [outbox.OutboxRelay(pool, eb, TABLE, batch_size=7) for _ in range(2)]

    async def drain(relay):
        while (await relay.run_once()).more:
            pass

    await asyncio.gather(*(drain(r) for r in relays))

    sent = [json.loads(m) for m in await stream_messages()]
    assert sorted(m["event_id"] for m in sent) == sorted(ids)  # без пропусков и без дублей
    conn = await asyncpg.connect(DB_URL)
    try:
        left = await conn.fetchval(f"SELECT count(*) FROM {TABLE} WHERE published_at IS NULL")
    finally:
        await conn.close()
    assert left == 0


async def test_resend_after_crash_is_deduplicated_by_jetstream(env):
    pool, eb = env
    await create_table()
    [eid] = await add_events(pool, 1)
    relay = outbox.OutboxRelay(pool, eb, TABLE)
    assert (await relay.run_once()).published == 1
    # «упали» после публикации, но до коммита: строка снова неотправленная
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute(f"UPDATE {TABLE} SET published_at = NULL")
    finally:
        await conn.close()
    assert (await relay.run_once()).published == 1
    msgs = await stream_messages()
    assert len(msgs) == 1 and eid in msgs[0].decode()


async def test_relay_waits_for_table_then_publishes(env):
    pool, eb = env
    metrics = outbox.OutboxMetrics.create(CollectorRegistry())
    relay = outbox.OutboxRelay(pool, eb, TABLE, metrics=metrics, interval_s=0.1)
    relay.start()
    try:
        await asyncio.sleep(0.3)  # таблицы ещё нет — relay ждёт, а не падает
        await create_table()
        await add_events(pool, 2)
        relay.kick()
        for _ in range(100):
            if len(await stream_messages()) == 2:
                break
            await asyncio.sleep(0.1)
        assert len(await stream_messages()) == 2
        assert metrics.published._value.get() == 2
    finally:
        await relay.stop()


async def test_cleanup_drops_only_old_published(env):
    pool, eb = env
    await create_table()
    await add_events(pool, 3)
    relay = outbox.OutboxRelay(pool, eb, TABLE, retention_s=3600)
    await relay.run_once()
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute(
            f"UPDATE {TABLE} SET published_at = now() - interval '2 hours' "
            f"WHERE id = (SELECT min(id) FROM {TABLE})"
        )
        await add_events(pool, 1)  # неотправленное не трогаем
        assert await relay.cleanup() == 1
        assert await conn.fetchval(f"SELECT count(*) FROM {TABLE}") == 3
    finally:
        await conn.close()


async def test_statement_timeout_is_applied(env):
    pool = await create_pool(DB_URL, 1, 1, timeout_s=0, statement_timeout_ms=200)
    try:
        async with pool.acquire() as conn:
            assert await conn.fetchval("SHOW statement_timeout") == "200ms"
            assert await conn.fetchval("SHOW idle_in_transaction_session_timeout") == "1min"
            with pytest.raises(asyncpg.QueryCanceledError):
                await conn.execute("SELECT pg_sleep(1)")
    finally:
        await pool.close()
