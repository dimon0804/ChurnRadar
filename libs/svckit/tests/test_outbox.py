"""Outbox и relay без живой инфраструктуры: таблица и шина — в памяти."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from prometheus_client import CollectorRegistry

from svckit import bus, db, outbox

T0 = datetime(2026, 10, 19, 15, 0, 0, 123000, tzinfo=UTC)


class FakeTable:
    """Строки outbox и разбор тех немногих запросов, что шлёт модуль."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.missing = False
        self.inserts: list[tuple] = []

    def add(self, subject: str, event_type: str, payload: dict) -> dict:
        row = {
            "id": len(self.rows) + 1,
            "event_id": uuid.uuid4(),
            "subject": subject,
            "type": event_type,
            "payload": json.dumps(payload),
            "created_at": T0,
            "published_at": None,
            "attempts": 0,
            "last_error": None,
        }
        self.rows.append(row)
        return row

    def pending(self) -> list[dict]:
        return [r for r in self.rows if r["published_at"] is None]


class FakeConn:
    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def _check(self) -> None:
        if self.table.missing:
            raise asyncpg.UndefinedTableError('relation "source.outbox" does not exist')

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                conn._check()
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Tx()

    async def execute(self, sql: str, *args):
        self._check()
        if sql.startswith("INSERT"):
            self.table.inserts.append((sql, args))
            return "INSERT 0 1"
        if sql.startswith("UPDATE"):
            row = next(r for r in self.table.rows if r["id"] == args[0])
            row["attempts"] += 1
            if "published_at = now()" in sql:
                row["published_at"] = datetime.now(UTC)
                row["last_error"] = None
            else:
                row["last_error"] = args[1]
            return "UPDATE 1"
        if sql.startswith("DELETE"):
            before = len(self.table.rows)
            self.table.rows = [r for r in self.table.rows if r["published_at"] is None]
            return f"DELETE {before - len(self.table.rows)}"
        raise AssertionError(f"неожиданный запрос: {sql}")

    async def fetch(self, sql: str, *args):
        self._check()
        assert "FOR UPDATE SKIP LOCKED" in sql and "ORDER BY id" in sql
        return self.table.pending()[: args[0]]

    async def fetchrow(self, sql: str, *args):
        self._check()
        pending = self.table.pending()
        return {"n": len(pending), "age": 3.0 if pending else None}


class FakePool:
    def __init__(self, table: FakeTable) -> None:
        self.conn = FakeConn(table)

    def acquire(self, timeout=None):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


class FakeBus:
    def __init__(self, connected: bool = True, fail_after: int | None = None) -> None:
        self.producer = "ingest"
        self.connected = connected
        self.fail_after = fail_after
        self.last_error: str | None = None
        self.sent: list[tuple[str, dict]] = []

    async def publish_envelope(self, subject, msg, *, retries=1, wait_connect=False):
        if not self.connected or (self.fail_after is not None and len(self.sent) >= self.fail_after):
            self.last_error = "TimeoutError: nats: timeout"
            return False
        self.sent.append((subject, msg))
        return True


def make_relay(table: FakeTable, fake_bus: FakeBus, **kw) -> tuple[outbox.OutboxRelay, outbox.OutboxMetrics]:
    metrics = outbox.OutboxMetrics.create(CollectorRegistry())
    relay = outbox.OutboxRelay(FakePool(table), fake_bus, "source.outbox", metrics=metrics, **kw)
    return relay, metrics


# --- запись ----------------------------------------------------------------------------------


async def test_add_event_inserts_text_payload_with_given_id():
    table = FakeTable()
    eid = await outbox.add_event(
        FakeConn(table), "source.outbox", "churn.source.changed", "source.changed", {"kind": "снимок"}
    )
    sql, args = table.inserts[0]
    assert "INSERT INTO source.outbox" in sql and "$4::text::jsonb" in sql
    assert args[0] == eid and str(uuid.UUID(eid)) == eid
    assert args[1:3] == ("churn.source.changed", "source.changed")
    assert json.loads(args[3]) == {"kind": "снимок"}


@pytest.mark.parametrize("name", ["outbox", "source.outbox; DROP TABLE x", "Source.Outbox", "a.b.c"])
def test_table_name_is_validated(name):
    with pytest.raises(ValueError):
        outbox.OutboxRelay(object(), FakeBus(), name)


# --- конверт ---------------------------------------------------------------------------------


def test_envelope_keeps_outbox_id_and_time():
    eid = str(uuid.uuid4())
    msg = bus.envelope("source.changed", {"kind": "changes"}, "ingest", event_id=eid, occurred_at=T0)
    assert set(msg) == {"event_id", "type", "occurred_at", "schema_version", "producer", "payload"}
    assert msg["event_id"] == eid
    assert msg["occurred_at"] == "2026-10-19T15:00:00.123000Z"
    assert msg["schema_version"] == 1 and msg["producer"] == "ingest"


async def test_publish_envelope_without_connection_returns_at_once():
    eb = bus.EventBus("nats://127.0.0.1:1", producer="t")
    started = asyncio.get_running_loop().time()
    ok = await eb.publish_envelope("churn.x", bus.envelope("x", {}, "t"), retries=3)
    assert ok is False and eb.last_error
    assert asyncio.get_running_loop().time() - started < 0.2


# --- relay: одна пачка -----------------------------------------------------------------------


async def test_run_once_publishes_in_order_and_marks_rows():
    table = FakeTable()
    first = table.add("churn.source.changed", "source.changed", {"kind": "snapshot", "account_ids": []})
    table.add("churn.source.changed", "source.changed", {"kind": "changes", "account_ids": [7]})
    fake_bus = FakeBus()
    relay, metrics = make_relay(table, fake_bus)

    result = await relay.run_once()

    assert (result.published, result.failed, result.more) == (2, False, False)
    assert [m["payload"]["kind"] for _, m in fake_bus.sent] == ["snapshot", "changes"]
    subject, msg = fake_bus.sent[0]
    assert subject == "churn.source.changed"
    assert msg["event_id"] == str(first["event_id"])  # он же Nats-Msg-Id
    assert msg["type"] == "source.changed" and msg["producer"] == "ingest"
    assert msg["occurred_at"] == "2026-10-19T15:00:00.123000Z"
    assert all(r["published_at"] is not None and r["attempts"] == 1 for r in table.rows)
    assert metrics.published._value.get() == 2
    assert metrics.pending._value.get() == 0


async def test_run_once_stops_on_first_failure():
    table = FakeTable()
    for i in range(3):
        table.add("churn.source.changed", "source.changed", {"n": i})
    relay, metrics = make_relay(table, FakeBus(fail_after=1))

    result = await relay.run_once()

    assert result.published == 1 and result.failed
    assert [r["published_at"] is not None for r in table.rows] == [True, False, False]
    assert table.rows[1]["attempts"] == 1 and "timeout" in table.rows[1]["last_error"]
    assert table.rows[2]["attempts"] == 0  # до третьего дело не дошло
    assert metrics.errors._value.get() == 1
    assert metrics.pending._value.get() == 2
    assert metrics.oldest_pending_s._value.get() == 3.0


async def test_run_once_reports_more_when_batch_is_full():
    table = FakeTable()
    for i in range(5):
        table.add("churn.source.changed", "source.changed", {"n": i})
    relay, _ = make_relay(table, FakeBus(), batch_size=2)
    assert (await relay.run_once()).more
    assert len(table.pending()) == 3


async def test_disconnected_bus_is_failure_only_with_pending_events():
    table = FakeTable()
    relay, _ = make_relay(table, FakeBus(connected=False))
    assert not (await relay.run_once()).failed
    table.add("churn.source.changed", "source.changed", {})
    assert (await relay.run_once()).failed
    assert table.rows[0]["attempts"] == 0  # без соединения строку не трогаем


async def test_cleanup_removes_published():
    table = FakeTable()
    table.add("s", "t", {})
    table.add("s", "t", {})
    relay, _ = make_relay(table, FakeBus())
    await relay.run_once()
    assert await relay.cleanup() == 2
    assert table.rows == []


# --- relay: фоновый цикл ---------------------------------------------------------------------


async def wait_for(cond, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("условие не выполнилось вовремя")
        await asyncio.sleep(0.01)


async def test_kick_wakes_relay_without_waiting_for_timer():
    table = FakeTable()
    fake_bus = FakeBus()
    relay, _ = make_relay(table, fake_bus, interval_s=60)
    relay.start()
    try:
        await asyncio.sleep(0.05)
        table.add("churn.settings.changed", "settings.changed", {"version": 2})
        relay.kick()
        await wait_for(lambda: len(fake_bus.sent) == 1, timeout=1)
    finally:
        await relay.stop()


async def test_relay_retries_until_nats_returns():
    table = FakeTable()
    table.add("churn.source.changed", "source.changed", {})
    fake_bus = FakeBus(connected=False)
    relay, _ = make_relay(table, fake_bus, interval_s=60, backoff_base_s=0.05, backoff_max_s=0.2)
    relay.start()
    try:
        await asyncio.sleep(0.3)
        assert fake_bus.sent == [] and len(table.pending()) == 1
        fake_bus.connected = True
        await wait_for(lambda: len(fake_bus.sent) == 1)
        await asyncio.sleep(0.1)
        assert len(fake_bus.sent) == 1  # ровно одна отправка
    finally:
        await relay.stop()


async def test_relay_waits_for_schema_instead_of_crashing():
    table = FakeTable()
    table.missing = True
    fake_bus = FakeBus()
    relay, _ = make_relay(table, fake_bus, interval_s=0.05)
    relay.start()
    try:
        await asyncio.sleep(0.2)
        assert relay._task is not None and not relay._task.done()
        table.missing = False
        table.add("churn.source.changed", "source.changed", {})
        await wait_for(lambda: len(fake_bus.sent) == 1)
    finally:
        await relay.stop()


async def test_relay_survives_unexpected_errors():
    table = FakeTable()
    table.add("churn.source.changed", "source.changed", {})
    fake_bus = FakeBus()
    relay, _ = make_relay(table, fake_bus, interval_s=60, backoff_base_s=0.01)
    calls = {"n": 0}
    original = relay.run_once

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionResetError("postgres ушёл")
        return await original()

    relay.run_once = flaky  # type: ignore[method-assign]
    relay.start()
    try:
        await wait_for(lambda: len(fake_bus.sent) == 1)
    finally:
        await relay.stop()


# --- таймауты сессии -------------------------------------------------------------------------


def test_session_settings():
    assert db.session_settings(30_000, 60_000) == {
        "statement_timeout": "30000",
        "idle_in_transaction_session_timeout": "60000",
    }
    assert db.session_settings(None, 0) == {"idle_in_transaction_session_timeout": "0"}
    assert db.session_settings(None, None) == {}


async def test_create_pool_passes_server_settings(monkeypatch):
    seen = {}

    async def fake_create_pool(dsn, **kw):
        seen.update(kw)
        return "pool"

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)
    assert await db.create_pool("postgresql://x", statement_timeout_ms=1500) == "pool"
    assert seen["server_settings"] == {
        "statement_timeout": "1500",
        "idle_in_transaction_session_timeout": "60000",
    }
