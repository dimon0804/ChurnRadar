"""Коннекторы против настоящего Postgres и фейковых HTTP-источников (биллинг и Logs API Метрики).

Проверяем путь данных целиком: запрос к источнику → apply_changes / агрегаты визитов → outbox,
курсор, статус и история, повторы и паузы после ошибок, лидерство двух реплик.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import asyncpg
import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from svckit.db import create_pool

from app.connectors import store
from app.connectors.http import RetryPolicy
from app.connectors.models import validate_options
from app.connectors.scheduler import LEADER_LOCK_KEY, RUN_LOCK_CLASS, Scheduler
from app.main import create_app
from app.repository import replace_snapshot
from app.schemas import SNAPSHOT
from tests.conftest import ADMIN
from tests.fake_sources import BillingState, MetricaState, Router, billing_app, metrica_app, tsv, visit_row
from tests.test_integration import reset_database

pytestmark = pytest.mark.integration

DB_URL = os.getenv("TEST_DATABASE_URL")
TODAY = date(2026, 10, 24)  # опорное время сида — 2026-10-19 15:00 UTC


async def fast_sleep(_: float) -> None:
    await asyncio.sleep(0)


@pytest.fixture
async def pool(sample, monkeypatch):
    if not DB_URL:
        pytest.skip("нужен TEST_DATABASE_URL")
    try:
        await reset_database(DB_URL)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres недоступен: {exc}")
    monkeypatch.setenv("CONNECTOR_TOKEN_BILLING", "billing-token")
    monkeypatch.setenv("CONNECTOR_TOKEN_METRICA", "metrica-token")
    db = await create_pool(DB_URL, 1, 6)
    seed = {**sample, "reference_time": "2026-10-19T15:00:00Z"}
    await replace_snapshot(db, SNAPSHOT.validate_python(seed), kind="seed", created_by="seed")
    await db.execute("DELETE FROM source.outbox")
    yield db
    await db.close()


@pytest.fixture
def sources():
    billing, metrica = BillingState(), MetricaState()
    transport = ASGITransport(app=Router({"billing.test": billing_app(billing), "metrica.test": metrica_app(metrica)}))
    return SimpleNamespace(billing=billing, metrica=metrica, transport=transport)


def scheduler(pool, sources, instance: str = "replica-a", **kwargs) -> Scheduler:
    return Scheduler(
        pool, DB_URL, tick_s=0.05, retry=RetryPolicy(attempts=3, base_delay_s=0.01, max_delay_s=0.02),
        failure_backoff_s=5.0, transport=sources.transport, today=lambda: TODAY, sleep=fast_sleep,
        instance=instance, **kwargs,
    )


async def add_connector(pool, connector_id: str, kind: str, **options) -> None:
    base = "http://billing.test/v1" if kind == "billing_http" else "http://metrica.test"
    token = "CONNECTOR_TOKEN_BILLING" if kind == "billing_http" else "CONNECTOR_TOKEN_METRICA"
    row = await store.create_connector(pool, {
        "id": connector_id, "kind": kind, "title": connector_id, "base_url": base, "token_env": token,
        "interval_s": 60, "enabled": True, "options": validate_options(kind, options),
    }, "test")
    assert row is not None


async def connector(pool, connector_id: str):
    return await store.get_connector(pool, connector_id)


async def outbox_events(pool) -> list[dict]:
    rows = await pool.fetch("SELECT payload FROM source.outbox ORDER BY id")
    return [r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"]) for r in rows]


def account(aid: int, balance: int, updated: str, email: str | None = None) -> dict:
    return {"id": aid, "email": email or f"user-{aid:04d}@example.test", "balance": balance, "withdrawBalance": 0,
            "totalReplenished": max(balance, 1), "bonusBalance": 0, "bonusState": "locked", "updatedAt": updated}


def service(sid: int, owner: int, updated: str, status: str = "active") -> dict:
    return {"id": sid, "name": f"vps-{sid}", "ownerId": owner, "paymentTerm": "month", "autoProlong": True,
            "createdAt": "2026-10-01T00:00:00Z", "expiresAt": "2026-11-01T00:00:00Z", "status": status,
            "price": 1500, "updatedAt": updated}


def transaction(tid: int, sid: int, updated: str) -> dict:
    return {"id": tid, "serviceId": sid, "amount": 1500, "status": "performed", "type": "order",
            "createdAt": updated, "performedAt": updated, "updatedAt": updated}


# --- биллинг ------------------------------------------------------------------------------


async def test_billing_pull_writes_through_apply_changes_and_moves_cursor(pool, sources):
    await add_connector(pool, "billing", "billing_http", page_size=1)
    sources.billing.accounts = [account(1, 777_000, "2026-10-20T10:00:00.000Z")]
    sources.billing.services = [service(9001, 1, "2026-10-20T10:00:01.000Z")]
    sources.billing.transactions = [transaction(9_000_001, 9001, "2026-10-20T10:00:02.000Z")]
    s = scheduler(pool, sources)
    try:
        assert await s.run_once("billing")
    finally:
        await s.stop()

    assert await pool.fetchval("SELECT balance FROM source.accounts WHERE id = 1") == 777_000
    assert await pool.fetchval("SELECT owner_id FROM source.services WHERE id = 9001") == 1
    assert await pool.fetchval("SELECT count(*) FROM source.transactions WHERE id = 9000001") == 1
    row = await connector(pool, "billing")
    assert row["last_status"] == "ok" and row["last_records"] == 3 and row["consecutive_failures"] == 0
    assert row["cursor"] == {
        "accounts": "2026-10-20T10:00:00Z",
        "services": "2026-10-20T10:00:01Z",
        "transactions": "2026-10-20T10:00:02Z",
    }
    assert row["next_run_at"] > datetime.now(UTC) + timedelta(seconds=50)
    events = await outbox_events(pool)
    assert events and events[-1]["kind"] == "changes" and 1 in events[-1]["account_ids"]
    [run] = await store.list_runs(pool, "billing", 5)
    assert run["status"] == "ok" and run["trigger"] == "schedule" and run["records"] == 3
    details = run["details"] if isinstance(run["details"], dict) else json.loads(run["details"])
    assert details["accounts"] == 1 and details["services"] == 1 and details["transactions"] == 1

    # второй запуск идёт с курсора; фильтр включительный, повтор безвреден
    sources.billing.requests.clear()
    s = scheduler(pool, sources)
    try:
        assert await s.run_once("billing")
    finally:
        await s.stop()
    first = sources.billing.requests[0]
    assert first["entity"] == "accounts" and first["updated_since"] == "2026-10-20T10:00:00Z"
    assert (await connector(pool, "billing"))["last_status"] == "ok"


async def test_billing_retries_transient_errors(pool, sources):
    await add_connector(pool, "billing", "billing_http")
    sources.billing.accounts = [account(2, 5, "2026-10-20T10:00:00Z")]
    sources.billing.fail_next = 2
    s = scheduler(pool, sources)
    try:
        await s.run_once("billing")
    finally:
        await s.stop()
    row = await connector(pool, "billing")
    assert row["last_status"] == "ok"
    [run] = await store.list_runs(pool, "billing", 5)
    assert run["attempts"] == 5  # accounts: 2 ошибки + успех, затем services и transactions
    assert await pool.fetchval("SELECT balance FROM source.accounts WHERE id = 2") == 5


async def test_billing_failure_sets_error_and_backoff(pool, sources):
    await add_connector(pool, "billing", "billing_http")
    sources.billing.fail_next = 100
    s = scheduler(pool, sources)
    try:
        await s.run_once("billing")
    finally:
        await s.stop()
    row = await connector(pool, "billing")
    assert row["last_status"] == "error" and "503" in row["last_error"]
    assert row["consecutive_failures"] == 1 and row["last_success_at"] is None
    # пауза 5 с · 2^0, а не интервал 60 с
    assert row["next_run_at"] < datetime.now(UTC) + timedelta(seconds=10)
    [run] = await store.list_runs(pool, "billing", 5)
    assert run["status"] == "error" and run["attempts"] == 3

    sources.billing.fail_next = 0
    sources.billing.token = "rotated"
    s = scheduler(pool, sources)
    try:
        await s.run_once("billing")
    finally:
        await s.stop()
    row = await connector(pool, "billing")
    assert "токен" in row["last_error"] and row["consecutive_failures"] == 2


async def test_billing_orphan_waits_for_its_owner(pool, sources):
    await add_connector(pool, "billing", "billing_http")
    sources.billing.services = [service(9100, 999, "2026-10-20T10:00:00Z")]
    s = scheduler(pool, sources)
    try:
        await s.run_once("billing")
        row = await connector(pool, "billing")
        assert row["last_status"] == "ok" and row["last_records"] == 0
        assert row["cursor"]["services"] == "2026-10-20T10:00:00Z"
        assert await pool.fetchval("SELECT count(*) FROM source.services WHERE id = 9100") == 0

        sources.billing.accounts = [account(999, 10, "2026-10-20T10:05:00Z")]
        await s.run_once("billing")
    finally:
        await s.stop()
    assert await pool.fetchval("SELECT owner_id FROM source.services WHERE id = 9100") == 999


async def test_disabled_connector_runs_only_on_request(pool, sources):
    await add_connector(pool, "billing", "billing_http")
    await pool.execute("UPDATE source.connectors SET enabled = false")
    s = scheduler(pool, sources)
    try:
        assert not await s.run_once("billing")
        await store.request_run(pool, "billing")
        assert await s.run_once("billing")
    finally:
        await s.stop()
    [run] = await store.list_runs(pool, "billing", 5)
    assert run["trigger"] == "manual"
    assert (await connector(pool, "billing"))["run_requested_at"] is None


# --- Метрика ------------------------------------------------------------------------------


async def test_metrica_run_builds_web_activity_and_feature_fires(pool, sources):
    await add_connector(pool, "metrica", "yandex_metrica", counter_id=100500, poll_interval_s=0.05)
    await pool.execute(
        "INSERT INTO source.metrica_client_ids (client_id, account_id) VALUES (16000000000000000001, 5)"
    )
    sources.metrica.tsv_parts = [
        tsv([
            visit_row(1, "2026-10-17 12:00:00", account=6, end="/services/61/delete"),
            visit_row(2, "2026-10-13 09:00:00", account=6, end="/services/61/delete"),
            visit_row(3, "2026-10-10 09:00:00", account=6),
        ]),
        tsv([
            visit_row(4, "2026-09-17 10:00:00", account=4),
            visit_row(5, "2026-10-18 10:00:00", client=16000000000000000001),
            visit_row(6, "2026-10-18 11:00:00", client=42),  # чужой браузер: не связан ни с кем
        ]),
    ]
    s = scheduler(pool, sources)
    try:
        assert await s.run_once("metrica")
    finally:
        await s.stop()

    row = await connector(pool, "metrica")
    assert row["last_status"] == "ok", row["last_error"]
    assert row["cursor"] == {"last_date": "2026-10-18"}
    [created] = sources.metrica.created
    assert created["date1"] == "2026-09-14" and created["date2"] == "2026-10-18" and created["source"] == "visits"
    assert "ym:s:parsedParamsKey2" in created["fields"]
    assert sources.metrica.cleaned == [1]

    web = {r["account_id"]: r for r in await pool.fetch("SELECT * FROM source.web_activity")}
    assert set(web) == {4, 5, 6}
    assert web[6]["cancel_visits_30d"] == 2 and web[6]["visits_30d"] == 3
    assert web[5]["visits_7d"] == 1
    assert web[4]["last_visit_at"] == datetime(2026, 9, 17, 7, tzinfo=UTC)
    events = await outbox_events(pool)
    assert sorted(events[-1]["account_ids"]) == [4, 5, 6]
    assert events[-1]["snapshot_id"].startswith("web_")

    # признаки модели по тем же агрегатам, что прочитает scoring
    from riskcore.registry import get_feature
    from riskcore.types import WebActivity
    from tests.web_ctx import ctx_for

    cols = ("account_id", "last_visit_at", "first_visit_at", "visits_7d", "visits_30d", "payment_visits_30d",
            "cancel_visits_30d", "pricing_visits_30d", "last_cancel_visit_at")
    as_of = datetime(2026, 10, 19, 15, tzinfo=UTC)
    cancel = get_feature("cancel_page_views").compute(ctx_for(WebActivity(*(web[6][c] for c in cols)), as_of))
    assert cancel is not None and abs(cancel.value - 0.8) < 1e-9
    absent = get_feature("panel_absence").compute(ctx_for(WebActivity(*(web[4][c] for c in cols)), as_of))
    assert absent is not None and absent.value > 0.3

    # повторный запуск перечитывает только последние дни, а сводка не меняется — события нет
    before = len(await outbox_events(pool))
    s = scheduler(pool, sources)
    try:
        await s.run_once("metrica")
    finally:
        await s.stop()
    assert sources.metrica.created[-1]["date1"] == "2026-10-16"
    assert len(await outbox_events(pool)) == before


async def test_metrica_processing_failed_is_reported(pool, sources):
    await add_connector(pool, "metrica", "yandex_metrica", counter_id=100500, poll_interval_s=0.05)
    sources.metrica.final_status = "processing_failed"
    s = scheduler(pool, sources)
    try:
        await s.run_once("metrica")
    finally:
        await s.stop()
    row = await connector(pool, "metrica")
    assert row["last_status"] == "error" and "processing_failed" in row["last_error"]
    assert row["cursor"] == {}


async def test_metrica_wrong_counter_is_access_error(pool, sources):
    await add_connector(pool, "metrica", "yandex_metrica", counter_id=1, poll_interval_s=0.05)
    s = scheduler(pool, sources)
    try:
        await s.run_once("metrica")
    finally:
        await s.stop()
    row = await connector(pool, "metrica")
    assert row["last_status"] == "error" and "403" in row["last_error"]


# --- лидерство ----------------------------------------------------------------------------


async def test_only_one_replica_executes(pool, sources):
    await add_connector(pool, "billing", "billing_http")
    sources.billing.accounts = [account(3, 42, "2026-10-20T10:00:00Z")]
    a, b = scheduler(pool, sources, "replica-a"), scheduler(pool, sources, "replica-b")
    try:
        await a.tick()
        await b.tick()
        assert a.is_leader and not b.is_leader
        await a.wait_idle()
        await b.wait_idle()
        runs = await store.list_runs(pool, "billing", 10)
        assert len(runs) == 1
        assert await pool.fetchval("SELECT instance FROM source.connector_runs") == "replica-a"

        # лидер пропал (оборвалась связь) — следующий тик второй реплики забирает лидерство
        await a._step_down()
        await b.tick()
        assert b.is_leader
        await store.request_run(pool, "billing")
        await b.tick()
        await b.wait_idle()
        assert len(await store.list_runs(pool, "billing", 10)) == 2
    finally:
        await a.stop()
        await b.stop()


async def test_run_lock_prevents_parallel_runs(pool, sources):
    await add_connector(pool, "billing", "billing_http")
    holder = await asyncpg.connect(DB_URL)
    try:
        assert await holder.fetchval("SELECT pg_try_advisory_lock($1, hashtext($2))", RUN_LOCK_CLASS, "billing")
        s = scheduler(pool, sources)
        try:
            assert not await s.run_once("billing")
        finally:
            await s.stop()
        assert await store.list_runs(pool, "billing", 5) == []
    finally:
        await holder.close()


async def test_new_leader_marks_abandoned_runs(pool, sources):
    await add_connector(pool, "billing", "billing_http")
    await pool.execute("UPDATE source.connectors SET enabled = false, last_status = 'running'")
    await pool.execute(
        "INSERT INTO source.connector_runs (connector_id, trigger, status) VALUES ('billing', 'schedule', 'running')"
    )
    s = scheduler(pool, sources)
    try:
        await s.tick()
        assert s.is_leader
    finally:
        await s.stop()
    [run] = await store.list_runs(pool, "billing", 5)
    assert run["status"] == "error" and "прерван" in run["error"]
    assert (await connector(pool, "billing"))["last_status"] == "error"


async def test_leader_lock_is_released_on_stop(pool, sources):
    s = scheduler(pool, sources)
    await s.tick()
    assert s.is_leader
    await s.stop()
    conn = await asyncpg.connect(DB_URL)
    try:
        assert await conn.fetchval("SELECT pg_try_advisory_lock($1)", LEADER_LOCK_KEY)
    finally:
        await conn.close()


# --- HTTP API -----------------------------------------------------------------------------


@pytest.fixture
async def api(pool):
    app = create_app(with_lifespan=False)
    app.state.pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_api_crud_run_and_history(api, pool):
    body = {"id": "erp", "kind": "billing_http", "title": "ERP", "base_url": "https://erp.example.com/api",
            "token_env": "CONNECTOR_TOKEN_BILLING", "interval_s": 120}
    created = await api.post("/api/v1/source/connectors", json=body, headers=ADMIN)
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["token_present"] and data["options"] == {"page_size": 500, "max_records": 50_000}
    assert data["status"]["next_run_at"] is None and not data["enabled"]
    assert "billing-token" not in created.text
    assert (await api.post("/api/v1/source/connectors", json=body, headers=ADMIN)).status_code == 409

    patched = await api.patch("/api/v1/source/connectors/erp", json={"enabled": True, "options": {"page_size": 100}},
                              headers=ADMIN)
    assert patched.status_code == 200, patched.text
    assert patched.json()["enabled"] and patched.json()["options"]["page_size"] == 100
    assert patched.json()["status"]["next_run_at"] is not None
    bad = await api.patch("/api/v1/source/connectors/erp", json={"options": {"page_size": 0}}, headers=ADMIN)
    assert bad.status_code == 422

    run = await api.post("/api/v1/source/connectors/erp/run", headers=ADMIN)
    assert run.status_code == 202 and run.json()["status"]["run_requested"]
    listed = (await api.get("/api/v1/source/connectors", headers=ADMIN)).json()
    assert [c["id"] for c in listed["items"]] == ["erp"] and listed["scheduler"]["enabled"] is False
    assert (await api.get("/api/v1/source/connectors/erp/runs", headers=ADMIN)).json() == {"items": []}

    await pool.execute("UPDATE source.connectors SET cursor = '{\"accounts\": \"x\"}'::jsonb")
    reset = await api.post("/api/v1/source/connectors/erp/reset-cursor", headers=ADMIN)
    assert reset.json()["cursor"] == {}
    assert (await api.delete("/api/v1/source/connectors/erp", headers=ADMIN)).status_code == 204
    assert (await api.get("/api/v1/source/connectors/erp", headers=ADMIN)).status_code == 404


async def test_api_managed_connector_fields_come_from_env(api, pool):
    settings = SimpleNamespace(
        connector_billing_url="", connector_billing_interval_s=60,
        connector_metrica_url="http://simulator:8000/demo/metrica", connector_metrica_counter=100500,
        connector_metrica_interval_s=3600,
    )
    conn = await asyncpg.connect(DB_URL)
    try:
        await store.bootstrap_from_settings(conn, settings)
        await pool.execute("UPDATE source.connectors SET enabled = true WHERE id = 'metrica'")
        settings.connector_metrica_url = "https://api-metrika.yandex.net"
        await store.bootstrap_from_settings(conn, settings)
    finally:
        await conn.close()
    row = (await api.get("/api/v1/source/connectors/metrica", headers=ADMIN)).json()
    # адрес из окружения обновился, включённость из интерфейса осталась
    assert row["managed"] and row["base_url"] == "https://api-metrika.yandex.net" and row["enabled"]
    assert row["options"]["counter_id"] == 100500 and row["token_env"] == "CONNECTOR_TOKEN_METRICA"
    locked = await api.patch("/api/v1/source/connectors/metrica", json={"base_url": "https://evil.example"},
                             headers=ADMIN)
    assert locked.status_code == 422 and "окружения" in locked.json()["error"]["message"]
    counter = await api.patch("/api/v1/source/connectors/metrica", json={"options": {"counter_id": 7}}, headers=ADMIN)
    assert counter.status_code == 422
    fine = await api.patch("/api/v1/source/connectors/metrica",
                           json={"interval_s": 600, "options": {"counter_id": 100500, "lookback_days": 20}},
                           headers=ADMIN)
    assert fine.status_code == 200, fine.text
    assert fine.json()["options"]["lookback_days"] == 20 and fine.json()["interval_s"] == 600


async def test_api_client_ids_upload_and_summary(api, pool):
    await add_connector(pool, "metrica", "yandex_metrica", counter_id=1)
    await pool.execute("UPDATE source.connectors SET cursor = '{\"last_date\": \"2026-10-18\"}'::jsonb")
    items = [{"client_id": "16000000000000000001", "account_id": 5}, {"client_id": "77", "account_id": 6}]
    resp = await api.post("/api/v1/source/metrica/client-ids", json={"items": items}, headers=ADMIN)
    assert resp.json() == {"accepted": 2, "total": 2}
    # новая связка — следующий запуск перечитает всю глубину
    assert (await connector(pool, "metrica"))["cursor"] == {}
    replaced = await api.post("/api/v1/source/metrica/client-ids",
                              json={"replace": True, "items": [{"client_id": "77", "account_id": 7}]}, headers=ADMIN)
    assert replaced.json() == {"accepted": 1, "total": 1}
    assert await pool.fetchval("SELECT account_id FROM source.metrica_client_ids") == 7
    too_big = await api.post("/api/v1/source/metrica/client-ids",
                             json={"items": [{"client_id": "99999999999999999999", "account_id": 1}]}, headers=ADMIN)
    assert too_big.status_code == 422
    summary = (await api.get("/api/v1/source/metrica", headers=ADMIN)).json()
    assert summary["client_ids"] == 1 and summary["accounts_with_activity"] == 0


async def test_scheduler_loop_picks_up_manual_run(pool, sources):
    await add_connector(pool, "billing", "billing_http")
    await pool.execute("UPDATE source.connectors SET enabled = false")
    sources.billing.accounts = [account(1, 31337, "2026-10-21T00:00:00Z")]
    s = scheduler(pool, sources)
    s.start()
    try:
        await store.request_run(pool, "billing")
        s.kick()
        for _ in range(100):
            if await pool.fetchval("SELECT balance FROM source.accounts WHERE id = 1") == 31337:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("ручной запуск не выполнился")
    finally:
        await s.stop()


def test_transport_type():
    assert issubclass(ASGITransport, httpx.AsyncBaseTransport)
