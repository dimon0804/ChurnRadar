"""Интеграция api с настоящими Postgres и NATS (и настоящим riskcore).

Нужны TEST_DATABASE_URL и TEST_NATS_URL.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import nats
import pytest
from httpx import ASGITransport, AsyncClient
from nats.js.errors import NotFoundError

from app.config import settings
from app.main import create_app
from tests.conftest import ADMIN, ANALYST

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_URL = os.getenv("TEST_DATABASE_URL")
NATS_URL = os.getenv("TEST_NATS_URL")
SCORED_AT = datetime(2026, 10, 19, 15, 0, 5, tzinfo=UTC)

# (id, email, balance, total_replenished, risk_score, level, expected_loss)
CUSTOMERS = [
    (1, "anna@example.test", 500, 9_000, 80.0, "high", 4_000),
    (2, "boris@example.test", 100, 1_000, None, "unknown", 0),
    (3, "clara@example.test", 900, 5_000, 40.0, "medium", 1_500),
    (4, "dmitry@example.test", 0, 7_000, 80.0, "high", 4_000),
    (5, "eva_x@example.test", 300, 3_000, 10.0, "low", 50),
    (6, "fedor@example.test", 700, 2_000, None, "unknown", 0),
    (7, "galina@other.test", 200, 8_000, 64.9, "medium", 2_500),
]


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


async def insert_scores(url: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        for cid, email, balance, repl, score, level, loss in CUSTOMERS:
            reasons = [] if score is None else [
                {"code": "prolong_wait", "title": "Услуга ждёт оплаты", "contribution": score, "service_id": cid * 10}
            ]
            detail = {
                "services": [{"id": cid * 10, "name": f"service-{cid}"}],
                "features": [],
                "transactions": [],
                "money": {"monthly_revenue": 1000, "exposure": loss, "expected_loss": loss,
                          "balance_coverage_days": 12.5, "explanation": "текст"},
                "recommendations": [{"code": "prolong_wait", "title": "Позвонить"}],
            }
            await conn.execute(
                """
                INSERT INTO scoring.customer_scores (customer_id, email, balance, bonus_balance, bonus_state,
                    withdraw_balance, total_replenished, risk_score, risk_level, loyalty, reasons,
                    services_total, services_at_risk, monthly_revenue, expected_loss, exposure, detail,
                    model_version, scored_at)
                VALUES ($1,$2,$3,0,'locked',0,$4,$5,$6,$7,$8::jsonb,1,$9,1000,$10,$10,$11::jsonb,1,$12)
                """,
                cid, email, balance, repl, score, level, None if score is None else 100 - score,
                json.dumps(reasons), 1 if level in ("high", "medium") else 0, loss, json.dumps(detail), SCORED_AT,
            )
    finally:
        await conn.close()


async def last_event(subject: str, where=None, timeout_s: float = 10.0) -> dict | None:
    """Последнее событие subject; ждём — relay outbox отправляет в фоне. Не дождались — None."""
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
                return None
            await asyncio.sleep(0.05)
    finally:
        await nc.close()


@pytest.fixture
async def client(monkeypatch):
    if not DB_URL or not NATS_URL:
        pytest.skip("нужны TEST_DATABASE_URL и TEST_NATS_URL")
    try:
        await reset_database(DB_URL)
        nc = await nats.connect(NATS_URL)
        try:
            await nc.jetstream().purge_stream("CHURN")
        except NotFoundError:
            pass
        await nc.close()
    except (OSError, asyncpg.PostgresError, nats.errors.NoServersError) as exc:
        pytest.skip(f"Postgres/NATS недоступны: {exc}")
    monkeypatch.setattr(settings, "database_url", DB_URL)
    monkeypatch.setattr(settings, "nats_url", NATS_URL)
    monkeypatch.setattr(settings, "summary_cache_ttl_s", 0.0)
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


def expected_order(sort: str, order: str) -> list[int]:
    idx = {"id": 0, "email": 1, "balance": 2, "total_replenished": 3, "risk_score": 4, "expected_loss": 6}[sort]
    rows = list(CUSTOMERS)
    if sort == "risk_score":
        scored = [r for r in rows if r[4] is not None]
        unknown = sorted((r for r in rows if r[4] is None), key=lambda r: r[0])
        scored.sort(key=lambda r: r[0])
        scored.sort(key=lambda r: r[4], reverse=(order == "desc"))
        return [r[0] for r in scored + unknown]
    # сортировка стабильна и при reverse=True, поэтому вторичный ключ id остаётся по возрастанию
    rows.sort(key=lambda r: r[0])
    rows.sort(key=lambda r: r[idx], reverse=(order == "desc"))
    return [r[0] for r in rows]


@pytest.mark.parametrize("sort", ["id", "email", "balance", "total_replenished", "risk_score", "expected_loss"])
@pytest.mark.parametrize("order", ["asc", "desc"])
async def test_sorting(client, sort, order):
    await insert_scores(DB_URL)
    resp = await client.get(f"/api/v1/customers?sort={sort}&order={order}&page_size=200", headers=ANALYST)
    assert resp.status_code == 200, resp.text
    ids = [i["id"] for i in resp.json()["items"]]
    assert ids == expected_order(sort, order)
    if sort == "risk_score":
        assert ids[-2:] == [2, 6]  # unknown всегда в конце


async def test_default_sort_and_page_meta(client):
    await insert_scores(DB_URL)
    body = (await client.get("/api/v1/customers?page_size=3&page=1", headers=ANALYST)).json()
    assert [i["id"] for i in body["items"]] == [1, 4, 7]  # 80, 80 (по id), 64.9
    assert body["total"] == 7 and body["page"] == 1 and body["page_size"] == 3
    assert body["scored_at"] == "2026-10-19T15:00:05Z"
    assert body["model_version"] == 1
    page3 = (await client.get("/api/v1/customers?page_size=3&page=3", headers=ANALYST)).json()
    assert [i["id"] for i in page3["items"]] == [6]
    item = body["items"][0]
    assert set(item) >= {"id", "email", "balance", "bonus_balance", "withdraw_balance", "total_replenished",
                         "risk_score", "risk_level", "loyalty", "reasons", "services_total", "services_at_risk",
                         "monthly_revenue", "expected_loss", "exposure"}
    assert item["reasons"][0]["service_id"] == 10


async def test_filter_and_search(client):
    await insert_scores(DB_URL)
    body = (await client.get("/api/v1/customers?level=high,unknown", headers=ANALYST)).json()
    assert [i["id"] for i in body["items"]] == [1, 4, 2, 6]
    assert body["total"] == 4
    by_email = (await client.get("/api/v1/customers?search=OTHER.test", headers=ANALYST)).json()
    assert [i["id"] for i in by_email["items"]] == [7]
    by_id = (await client.get("/api/v1/customers?search=5", headers=ANALYST)).json()
    assert [i["id"] for i in by_id["items"]] == [5]
    underscore = (await client.get("/api/v1/customers?search=a_x", headers=ANALYST)).json()
    assert [i["id"] for i in underscore["items"]] == [5]
    bad = await client.get("/api/v1/customers?level=extreme", headers=ANALYST)
    assert bad.status_code == 422


async def test_customer_detail(client):
    await insert_scores(DB_URL)
    body = (await client.get("/api/v1/customers/3", headers=ANALYST)).json()
    assert body["id"] == 3 and body["risk_level"] == "medium"
    assert body["services"][0]["id"] == 30
    assert body["money"]["balance_coverage_days"] == 12.5
    assert body["recommendations"][0]["title"] == "Позвонить"
    assert body["scored_at"] == "2026-10-19T15:00:05Z" and body["model_version"] == 1
    assert (await client.get("/api/v1/customers/999", headers=ANALYST)).status_code == 404


async def test_summary_empty_then_filled(client):
    empty = await client.get("/api/v1/analytics/summary", headers=ANALYST)
    assert empty.status_code == 200
    assert empty.json()["scored_at"] is None and empty.json()["customers_total"] == 0

    data = {"customers_total": 7, "by_level": {"high": 2, "medium": 2, "low": 1, "unknown": 2},
            "services_total": 7, "services_at_risk": 4, "expected_loss": 12050, "exposure": 12050,
            "monthly_revenue_total": 7000, "revenue_at_risk_share": 0.5, "horizon_days": 30,
            "money_explanation": "x", "top_reasons": [], "timeline": [],
            "risk_scores": {"scored": 5, "avg": 61.25, "max": 90.5,
                            "buckets": [{"le": le, "count": min(5, max(0, (le - 40) // 10))}
                                        for le in range(10, 101, 10)]}}
    conn = await asyncpg.connect(DB_URL)
    await conn.execute("INSERT INTO scoring.summary (id, data, model_version, scored_at) VALUES (1, $1::jsonb, 3, $2)",
                       json.dumps(data), SCORED_AT)
    await conn.close()
    filled = (await client.get("/api/v1/analytics/summary", headers=ANALYST)).json()
    assert filled["customers_total"] == 7 and filled["model_version"] == 3
    assert filled["scored_at"] == "2026-10-19T15:00:05Z"

    metrics = await client.get("/metrics", headers={"Authorization": "Bearer churn-metrics-token"})
    assert "churn_expected_loss_eur 120.5" in metrics.text
    assert 'churn_customers_by_risk_level{level="unknown"} 2.0' in metrics.text
    assert "churn_model_version 3.0" in metrics.text
    # risk-score в /metrics — те же числа, что risk_scores в сводке API
    stats = filled["risk_scores"]
    assert f"churn_risk_score_avg {stats['avg']}" in metrics.text
    assert f"churn_risk_score_max {stats['max']}" in metrics.text
    for bucket in stats["buckets"]:
        assert f'churn_risk_score_bucket{{le="{bucket["le"]}"}} {float(bucket["count"])}' in metrics.text
    assert f'churn_risk_score_bucket{{le="+Inf"}} {float(stats["scored"])}' in metrics.text
    assert "churn_revenue_at_risk_ratio 0.5" in metrics.text


async def test_settings_flow(client):
    first = await client.get("/api/v1/model/settings", headers=ANALYST)
    assert first.status_code == 200
    s = first.json()
    assert s["version"] == 1 and s["horizon_days"] == 30
    codes = {w["code"] for w in s["weights"]}
    assert {"prolong_wait", "autoprolong_off_expiring", "insufficient_funds"} <= codes

    denied = await client.patch("/api/v1/model/settings", json={"horizon_days": 45}, headers=ANALYST)
    assert denied.status_code == 403

    patched = await client.patch(
        "/api/v1/model/settings",
        json={"horizon_days": 45, "weights": [{"code": "suspended", "weight": 12.5, "enabled": False}]},
        headers=ADMIN,
    )
    assert patched.status_code == 200, patched.text
    p = patched.json()
    assert p["version"] == 2 and p["horizon_days"] == 45 and p["updated_by"] == "admin"
    sus = next(w for w in p["weights"] if w["code"] == "suspended")
    assert sus["weight"] == 12.5 and sus["enabled"] is False

    event = await last_event("churn.settings.changed", lambda e: e["payload"]["version"] == 2)
    assert event is not None
    assert event["type"] == "settings.changed" and event["producer"] == "api"
    assert event["payload"] == {"version": 2, "updated_by": "admin"}

    same = (await client.patch("/api/v1/model/settings", json={"horizon_days": 45}, headers=ADMIN)).json()
    assert same["version"] == 2  # без изменений версия не растёт

    bad = await client.patch("/api/v1/model/settings", json={"thresholds": {"medium": 90}}, headers=ADMIN)
    assert bad.status_code == 422
    unknown = await client.patch("/api/v1/model/settings", json={"weights": [{"code": "zzz", "weight": 1}]}, headers=ADMIN)
    assert unknown.status_code == 422

    reset = (await client.post("/api/v1/model/settings/reset", headers=ADMIN)).json()
    assert reset["version"] == 3 and reset["horizon_days"] == 30
    assert next(w for w in reset["weights"] if w["code"] == "suspended")["enabled"] is True

    history = (await client.get("/api/v1/model/settings/history?limit=10", headers=ANALYST)).json()["items"]
    assert [h["version"] for h in history] == [3, 2]
    v2_paths = {c["path"] for c in history[1]["changes"]}
    assert v2_paths == {"horizon_days", "weights.suspended.weight", "weights.suspended.enabled"}
    assert history[0]["changed_by"] == "admin" and history[0]["changed_at"].endswith("Z")


async def test_settings_event_waits_in_outbox_while_nats_is_down(monkeypatch):
    """NATS лежит: PATCH проходит, событие в scoring.outbox; NATS вернулся — оно уходит один раз."""
    if not DB_URL or not NATS_URL:
        pytest.skip("нужны TEST_DATABASE_URL и TEST_NATS_URL")
    from svckit.testing import TcpGate

    try:
        await reset_database(DB_URL)
        nc = await nats.connect(NATS_URL)
        try:
            await nc.jetstream().purge_stream("CHURN")
        except NotFoundError:
            pass
        await nc.close()
    except (OSError, asyncpg.PostgresError, nats.errors.NoServersError) as exc:
        pytest.skip(f"Postgres/NATS недоступны: {exc}")
    gate = TcpGate(NATS_URL)
    monkeypatch.setattr(settings, "database_url", DB_URL)
    monkeypatch.setattr(settings, "nats_url", gate.url)
    monkeypatch.setattr(settings, "nats_wait_on_start_s", 0.5)
    app = create_app()
    try:
        async with app.router.lifespan_context(app), AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch("/api/v1/model/settings", json={"horizon_days": 60}, headers=ADMIN)
            assert resp.status_code == 200, resp.text
            assert resp.json()["version"] == 2

            conn = await asyncpg.connect(DB_URL)
            try:
                rows = await conn.fetch("SELECT event_id, payload::text p, published_at FROM scoring.outbox")
                assert len(rows) == 1 and rows[0]["published_at"] is None
                assert json.loads(rows[0]["p"]) == {"version": 2, "updated_by": "admin"}
                assert await last_event("churn.settings.changed", timeout_s=0.5) is None

                await gate.open()
                event = await last_event("churn.settings.changed", timeout_s=30)
                assert event is not None and event["event_id"] == str(rows[0]["event_id"])
                assert event["payload"] == {"version": 2, "updated_by": "admin"} and event["producer"] == "api"
                await asyncio.sleep(0.5)
                nc = await nats.connect(NATS_URL)
                try:
                    info = await nc.jetstream().stream_info("CHURN", subjects_filter="churn.settings.changed")
                    assert (info.state.subjects or {}).get("churn.settings.changed") == 1
                finally:
                    await nc.close()
                assert await conn.fetchval("SELECT count(*) FROM scoring.outbox WHERE published_at IS NULL") == 0
            finally:
                await conn.close()
    finally:
        await gate.close()


async def _sql(query: str, *args) -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute(query, *args)
    finally:
        await conn.close()


def _csv(resp) -> list[list[str]]:
    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(b"\xef\xbb\xbf")
    return [line.split(";") for line in resp.content.decode("utf-8-sig").split("\r\n") if line]


async def test_call_today_marks_and_export(client):
    await insert_scores(DB_URL)
    queue = (await client.get("/api/v1/call-today?limit=10", headers=ANALYST)).json()
    # без unknown, по убыванию ожидаемой потери, при равенстве — по id
    assert [i["id"] for i in queue["items"]] == [1, 4, 7, 3, 5]
    assert queue["queue_total"] == 5 and queue["marked_total"] == 0 and queue["queue_expected_loss"] == 12_050
    first = queue["items"][0]
    assert first["main_reason"] == "Услуга ждёт оплаты" and first["action"] is None
    assert first["next_action"] == {"code": "prolong_wait", "title": "Позвонить"}
    assert len((await client.get("/api/v1/call-today", headers=ANALYST)).json()["items"]) == 5
    assert len((await client.get("/api/v1/call-today?limit=2", headers=ANALYST)).json()["items"]) == 2
    # короткое название причины (label) предпочитаем длинному объяснению
    await _sql("""UPDATE scoring.customer_scores SET reasons = jsonb_set(reasons, '{0,label}', '"Ждёт"')
                  WHERE customer_id = 3""")
    items = (await client.get("/api/v1/call-today?limit=10", headers=ANALYST)).json()["items"]
    assert next(i for i in items if i["id"] == 3)["main_reason"] == "Ждёт"

    # аналитик отмечает «Сделано» — клиент уходит из очереди в «отмеченные»
    done = await client.put("/api/v1/customers/1/action", json={"status": "done", "note": " позвонили "}, headers=ANALYST)
    assert done.status_code == 200, done.text
    action = done.json()["action"]
    assert action["status"] == "done" and action["active"] is True and action["updated_by"] == "analyst"
    assert action["note"] == "позвонили" and action["expected_loss_at_mark"] == 4_000
    snoozed = await client.put("/api/v1/customers/4/action", json={"status": "snoozed", "snooze_days": 3}, headers=ADMIN)
    assert snoozed.status_code == 200 and snoozed.json()["action"]["snoozed_until"].endswith("Z")
    queue = (await client.get("/api/v1/call-today", headers=ANALYST)).json()
    assert [i["id"] for i in queue["items"]] == [7, 3, 5] and queue["marked_total"] == 2
    marked = (await client.get("/api/v1/call-today?marked=true", headers=ANALYST)).json()
    assert [i["id"] for i in marked["items"]] == [1, 4]

    past = await client.put(
        "/api/v1/customers/7/action", json={"status": "snoozed", "snoozed_until": "2020-01-01T00:00:00Z"}, headers=ANALYST
    )
    assert past.status_code == 422
    assert (await client.put("/api/v1/customers/999/action", json={"status": "done"}, headers=ANALYST)).status_code == 404
    assert (await client.get("/api/v1/customers/999/action", headers=ANALYST)).status_code == 404

    # CSV: те же фильтры и сортировка, что у списка, плюс статус
    rows = _csv(await client.get("/api/v1/customers/export.csv?level=high,medium&sort=expected_loss&order=desc", headers=ANALYST))
    assert rows[0] == ["ID", "Email", "Уровень риска", "Риск, баллы", "Ожидаемая потеря, €", "Баланс, €",
                       "Пополнения всего, €", "Главная причина", "Что сделать", "Статус"]
    assert [r[0] for r in rows[1:]] == ["1", "4", "7", "3"]
    assert rows[1] == ["1", "anna@example.test", "Высокий", "80,0", "40,00", "5,00", "90,00",
                       "Услуга ждёт оплаты", "Позвонить", "Сделано"]
    assert rows[2][9].startswith("Отложено до ")
    by_search = _csv(await client.get("/api/v1/customers/export.csv?search=other.test", headers=ANALYST))
    assert [r[0] for r in by_search[1:]] == ["7"]
    worklist = _csv(await client.get("/api/v1/customers/export.csv?worklist=true&sort=expected_loss", headers=ANALYST))
    assert [r[0] for r in worklist[1:]] == ["7", "3", "5"]
    everyone = _csv(await client.get("/api/v1/customers/export.csv", headers=ANALYST))
    assert [r[0] for r in everyone[1:]] == ["1", "4", "7", "3", "5", "2", "6"]  # как список по умолчанию

    # отложка истекла — клиент снова в очереди
    await _sql("UPDATE scoring.customer_actions SET snoozed_until = now() - interval '1 second' WHERE customer_id = 4")
    assert [i["id"] for i in (await client.get("/api/v1/call-today", headers=ANALYST)).json()["items"]] == [4, 7, 3, 5]

    # после «Сделано» потеря выросла — тоже возвращаем, с пометкой
    await _sql("UPDATE scoring.customer_scores SET expected_loss = 5000 WHERE customer_id = 1")
    queue = (await client.get("/api/v1/call-today", headers=ANALYST)).json()
    assert [i["id"] for i in queue["items"]][:2] == [1, 4]
    assert queue["items"][0]["action"]["returned"] is True and queue["items"][0]["action"]["active"] is False

    state = (await client.get("/api/v1/customers/1/action", headers=ANALYST)).json()
    assert [h["status"] for h in state["history"]] == ["done"]
    assert state["history"][0]["changed_by"] == "analyst" and state["history"][0]["changed_at"].endswith("Z")

    cleared = await client.delete("/api/v1/customers/1/action", headers=ANALYST)
    assert cleared.status_code == 200 and cleared.json()["action"] is None
    assert [h["status"] for h in cleared.json()["history"]] == ["cleared", "done"]
    again = await client.delete("/api/v1/customers/1/action", headers=ANALYST)
    assert [h["status"] for h in again.json()["history"]] == ["cleared", "done"]  # повторное снятие журнал не засоряет
