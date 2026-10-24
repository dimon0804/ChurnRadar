"""Интеграция с настоящими Postgres и NATS JetStream.

Нужны TEST_DATABASE_URL и TEST_NATS_URL, например:
    TEST_DATABASE_URL=postgresql://churn:churn@localhost:55432/churn
    TEST_NATS_URL=nats://localhost:54222
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import os
import sys

import asyncpg
import nats
import pytest
from httpx import ASGITransport, AsyncClient
from nats.js.errors import NotFoundError

from app.config import settings
from app.main import create_app
from tests.conftest import ADMIN, REPO_ROOT, SAMPLE_PATH

pytestmark = pytest.mark.integration

DB_URL = os.getenv("TEST_DATABASE_URL")
NATS_URL = os.getenv("TEST_NATS_URL")


def _load_migrator():
    spec = importlib.util.spec_from_file_location("migrate", REPO_ROOT / "services" / "migrate" / "migrate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate"] = module
    spec.loader.exec_module(module)
    return module


async def reset_database(url: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(
            "DROP SCHEMA IF EXISTS source CASCADE; DROP SCHEMA IF EXISTS scoring CASCADE;"
            "DROP TABLE IF EXISTS public.schema_migrations;"
        )
    finally:
        await conn.close()
    await _load_migrator().run(url, [str(REPO_ROOT / "db" / "core")], connect_timeout_s=10)


async def purge_stream(url: str) -> None:
    nc = await nats.connect(url)
    try:
        js = nc.jetstream()
        try:
            await js.purge_stream("CHURN")
        except NotFoundError:
            pass
    finally:
        await nc.close()


async def last_event(subject: str, where=None, timeout_s: float = 10.0) -> dict:
    """Последнее событие subject; ждём его — relay outbox отправляет в фоне."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    nc = await nats.connect(NATS_URL)
    try:
        while True:
            try:
                msg = await nc.jetstream().get_last_msg("CHURN", subject)
                event = json.loads(msg.data)
                if where is None or where(event):
                    return event
            except NotFoundError:
                pass
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"событие {subject} не пришло за {timeout_s} с")
            await asyncio.sleep(0.05)
    finally:
        await nc.close()


async def stream_count(subject: str) -> int:
    nc = await nats.connect(NATS_URL)
    try:
        info = await nc.jetstream().stream_info("CHURN", subjects_filter=subject)
        return (info.state.subjects or {}).get(subject, 0)
    except NotFoundError:
        return 0
    finally:
        await nc.close()


@pytest.fixture
async def env(monkeypatch):
    if not DB_URL or not NATS_URL:
        pytest.skip("нужны TEST_DATABASE_URL и TEST_NATS_URL")
    try:
        await reset_database(DB_URL)
        await purge_stream(NATS_URL)
    except (OSError, asyncpg.PostgresError, nats.errors.NoServersError) as exc:
        pytest.skip(f"Postgres/NATS недоступны: {exc}")
    monkeypatch.setattr(settings, "database_url", DB_URL)
    monkeypatch.setattr(settings, "nats_url", NATS_URL)
    monkeypatch.setattr(settings, "seed_path", str(SAMPLE_PATH))
    monkeypatch.setattr(settings, "seed_on_start", True)
    yield


@pytest.fixture
async def running(env):
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield app, client


async def fetch(query: str, *args):
    conn = await asyncpg.connect(DB_URL)
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


async def test_migrator_is_idempotent(env):
    migrator = _load_migrator()
    applied = await migrator.run(DB_URL, [str(REPO_ROOT / "db" / "core"), "/no/such/dir"], connect_timeout_s=5)
    assert applied == []
    rows = await fetch("SELECT name FROM public.schema_migrations")
    expected = sorted(f"core/{p.name}" for p in (REPO_ROOT / "db" / "core").glob("*.sql"))
    assert sorted(r["name"] for r in rows) == expected


async def test_seed_on_start_and_event(running):
    app, client = running
    counts = (await fetch(
        "SELECT (SELECT count(*) FROM source.accounts) a, (SELECT count(*) FROM source.services) s,"
        " (SELECT count(*) FROM source.transactions) t, (SELECT reference_time FROM source.clock) ref"
    ))[0]
    assert (counts["a"], counts["s"], counts["t"]) == (10, 30, 100)
    assert counts["ref"].isoformat() == "2026-10-19T15:00:00+00:00"

    snaps = await fetch("SELECT kind, created_by FROM source.snapshots")
    assert [(r["kind"], r["created_by"]) for r in snaps] == [("seed", "seed")]

    hist = await fetch("SELECT * FROM source.status_history")
    assert hist, "для удалённых услуг должна быть оценочная история"
    assert all(r["estimated"] and r["new_status"] == "deleted" for r in hist)

    event = await last_event("churn.source.changed")
    assert event["type"] == "source.changed"
    assert event["schema_version"] == 1
    assert event["producer"] == "ingest"
    assert event["payload"]["kind"] == "snapshot"
    assert event["payload"]["counts"] == {"accounts": 10, "services": 30, "transactions": 100}
    assert event["payload"]["account_ids"] == []

    ready = await client.get("/readyz")
    assert ready.json() == {"status": "ok", "checks": {"db": True, "nats": True}}
    assert (await client.get("/healthz")).json() == {"status": "ok"}


async def test_seed_skipped_when_data_exists(running):
    app, _ = running
    from app.main import seed_if_empty

    assert await seed_if_empty(app) is None
    assert len(await fetch("SELECT 1 FROM source.snapshots")) == 1


async def test_snapshot_replace_records_status_diff(running, sample):
    _, client = running
    snap = copy.deepcopy(sample)
    target = next(s for s in snap["services"] if s["status"] == "active")
    target["status"] = "prolong_wait"
    snap["reference_time"] = "2026-10-20T10:00:00Z"
    snap["transactions"] = snap["transactions"][:50]

    resp = await client.put("/api/v1/source/snapshot", json=snap, headers=ADMIN)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["counts"] == {"accounts": 10, "services": 30, "transactions": 50}
    assert body["snapshot_id"].startswith("snap_")

    rows = await fetch("SELECT * FROM source.status_history WHERE NOT estimated")
    assert [(r["service_id"], r["old_status"], r["new_status"]) for r in rows] == [
        (target["id"], "active", "prolong_wait")
    ]
    assert rows[0]["changed_at"].isoformat() == "2026-10-20T10:00:00+00:00"
    assert (await fetch("SELECT count(*) c FROM source.transactions"))[0]["c"] == 50

    event = await last_event("churn.source.changed", lambda e: e["payload"]["snapshot_id"] == body["snapshot_id"])
    assert event["payload"]["kind"] == "snapshot"


async def test_snapshot_reset_history_starts_clean(running, sample):
    _, client = running
    snap = copy.deepcopy(sample)
    target = next(s for s in snap["services"] if s["status"] == "active")
    target["status"] = "prolong_wait"
    snap["reference_time"] = "2026-10-20T10:00:00Z"

    resp = await client.put(
        "/api/v1/source/snapshot", params={"reset_history": "true"}, json=snap, headers=ADMIN
    )
    assert resp.status_code == 202, resp.text
    # Смены статусов против прежнего набора не пишутся, остаются только оценочные удаления.
    assert (await fetch("SELECT count(*) c FROM source.status_history WHERE NOT estimated"))[0]["c"] == 0
    estimated = await fetch("SELECT count(*) c FROM source.status_history WHERE estimated")
    assert estimated[0]["c"] == sum(1 for s in snap["services"] if s["status"] == "deleted")


async def test_events_upsert(running, sample):
    _, client = running
    svc = dict(next(s for s in sample["services"] if s["status"] == "active"), status="suspended")
    acc = dict(sample["accounts"][2], balance=1)
    tx = {
        "id": 10_001, "amount": 500, "status": "performed", "performedAt": "2026-10-19T14:00:00Z",
        "createdAt": "2026-10-19T14:00:00Z", "type": "prolong", "serviceId": sample["services"][5]["id"],
    }
    new_svc = dict(sample["services"][0], id=5_000, status="active")
    resp = await client.post(
        "/api/v1/source/events",
        json={"accounts": [acc], "services": [svc, new_svc], "transactions": [tx]},
        headers=ADMIN,
    )
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"accepted": 4}

    assert (await fetch("SELECT balance FROM source.accounts WHERE id=$1", acc["id"]))[0]["balance"] == 1
    assert (await fetch("SELECT status FROM source.services WHERE id=$1", svc["id"]))[0]["status"] == "suspended"
    assert (await fetch("SELECT count(*) c FROM source.transactions WHERE id=10001"))[0]["c"] == 1
    hist = await fetch("SELECT service_id, old_status, new_status FROM source.status_history WHERE NOT estimated ORDER BY id")
    assert [(r["service_id"], r["old_status"], r["new_status"]) for r in hist] == [
        (svc["id"], "active", "suspended"),
        (5_000, None, "active"),
    ]

    event = await last_event("churn.source.changed", lambda e: e["payload"]["kind"] == "changes")
    payload = event["payload"]
    expected = sorted({acc["id"], svc["ownerId"], new_svc["ownerId"], sample["services"][5]["ownerId"]})
    assert payload["account_ids"] == expected


async def test_events_broken_reference_rolls_back(running, sample):
    _, client = running
    acc = dict(sample["accounts"][0], balance=42)
    bad_tx = {
        "id": 20_000, "amount": 1, "status": "performed", "performedAt": None,
        "createdAt": "2026-10-19T14:00:00Z", "type": "order", "serviceId": 999_999,
    }
    resp = await client.post("/api/v1/source/events", json={"accounts": [acc], "transactions": [bad_tx]}, headers=ADMIN)
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["type"] == "broken_reference"
    assert (await fetch("SELECT balance FROM source.accounts WHERE id=$1", acc["id"]))[0]["balance"] != 42


async def test_events_email_taken(running, sample):
    _, client = running
    acc = dict(sample["accounts"][0], email=sample["accounts"][1]["email"])
    resp = await client.post("/api/v1/source/events", json={"accounts": [acc]}, headers=ADMIN)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_seed_waits_for_schema(env):
    import asyncio

    from app.main import seed_when_schema_ready

    calls = []

    class Fake:
        pass

    import app.main as main_mod

    async def flaky(app):
        calls.append(1)
        if len(calls) < 3:
            raise asyncpg.UndefinedTableError("relation \"source.accounts\" does not exist")
        return None

    original = main_mod.seed_if_empty
    main_mod.seed_if_empty = flaky
    try:
        await asyncio.wait_for(seed_when_schema_ready(Fake(), attempts=5, pause_s=0.01), timeout=5)
    finally:
        main_mod.seed_if_empty = original
    assert len(calls) == 3


# --- outbox: NATS пропадает и возвращается --------------------------------------------------

SUBJECT = "churn.source.changed"


async def wait_until(cond, timeout_s: float = 15.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("условие не выполнилось вовремя")
        await asyncio.sleep(0.05)


async def outbox_rows():
    return await fetch("SELECT event_id, payload, published_at, attempts FROM source.outbox ORDER BY id")


@pytest.fixture
async def gate(env, monkeypatch):
    """NATS для сервиса — через закрываемый прокси; сид выключен, чтобы событие было одно."""
    from svckit.testing import TcpGate

    g = TcpGate(NATS_URL)
    monkeypatch.setattr(settings, "nats_url", g.url)
    monkeypatch.setattr(settings, "nats_wait_on_start_s", 0.5)
    monkeypatch.setattr(settings, "seed_on_start", False)
    yield g
    await g.close()


async def test_nats_down_on_write_event_survives_in_outbox(gate, sample):
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert not app.state.bus.connected
        resp = await client.put("/api/v1/source/snapshot", json=sample, headers=ADMIN)
        assert resp.status_code == 202, resp.text
        snapshot_id = resp.json()["snapshot_id"]

        # данные записаны, событие ждёт в outbox
        assert (await fetch("SELECT count(*) c FROM source.accounts"))[0]["c"] == 10
        rows = await outbox_rows()
        assert len(rows) == 1 and rows[0]["published_at"] is None
        payload = json.loads(rows[0]["payload"])
        assert payload == {
            "kind": "snapshot", "snapshot_id": snapshot_id,
            "counts": {"accounts": 10, "services": 30, "transactions": 100}, "account_ids": [],
        }
        await asyncio.sleep(1.0)
        assert await stream_count(SUBJECT) == 0

        # NATS появился — событие уходит само
        await gate.open()
        event = await last_event(SUBJECT, lambda e: e["payload"]["snapshot_id"] == snapshot_id, timeout_s=30)
        assert event["event_id"] == str(rows[0]["event_id"])
        assert event["type"] == "source.changed" and event["producer"] == "ingest"
        assert event["schema_version"] == 1 and event["payload"] == payload

        # лишний пинок и время на повтор — дубля нет
        app.state.relay.kick()
        await asyncio.sleep(1.0)
        assert await stream_count(SUBJECT) == 1
        [row] = await outbox_rows()
        assert row["published_at"] is not None


async def test_nats_lost_after_start_event_sent_on_return(gate, sample):
    await gate.open()
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await wait_until(lambda: app.state.bus.connected)
        await gate.close()
        await wait_until(lambda: not app.state.bus.connected)

        snap = await client.put("/api/v1/source/snapshot", json=sample, headers=ADMIN)
        assert snap.status_code == 202, snap.text
        acc = dict(sample["accounts"][0], balance=7)
        resp = await client.post("/api/v1/source/events", json={"accounts": [acc]}, headers=ADMIN)
        assert resp.status_code == 202, resp.text
        assert (await fetch("SELECT balance FROM source.accounts WHERE id=$1", acc["id"]))[0]["balance"] == 7
        assert [r["published_at"] for r in await outbox_rows()] == [None, None]

        await gate.open()
        event = await last_event(SUBJECT, lambda e: e["payload"]["kind"] == "changes", timeout_s=30)
        assert event["payload"]["account_ids"] == [acc["id"]]
        await asyncio.sleep(0.5)
        assert await stream_count(SUBJECT) == 2  # снимок и изменения, по одному разу
        assert all(r["published_at"] is not None for r in await outbox_rows())
