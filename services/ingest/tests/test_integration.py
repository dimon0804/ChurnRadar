"""Интеграция с настоящими Postgres и NATS JetStream.

Нужны TEST_DATABASE_URL и TEST_NATS_URL, например:
    TEST_DATABASE_URL=postgresql://churn:churn@localhost:55432/churn
    TEST_NATS_URL=nats://localhost:54222
"""

from __future__ import annotations

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


async def last_event(subject: str) -> dict:
    nc = await nats.connect(NATS_URL)
    try:
        msg = await nc.jetstream().get_last_msg("CHURN", subject)
        return json.loads(msg.data)
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
    assert [r["name"] for r in rows] == ["core/001_core.sql"]


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

    event = await last_event("churn.source.changed")
    assert event["payload"]["snapshot_id"] == body["snapshot_id"]


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

    event = await last_event("churn.source.changed")
    payload = event["payload"]
    assert payload["kind"] == "changes"
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
