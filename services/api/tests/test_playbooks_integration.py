"""Сценарии на настоящем Postgres и фейковом SMTP: пересчёт → срабатывание → письмо, контроль, эффект."""

from __future__ import annotations

import asyncio

import asyncpg
import nats
import pytest
from httpx import ASGITransport, AsyncClient
from nats.js.errors import NotFoundError

from app import playbook_rules as rules
from app.config import settings
from app.main import create_app
from tests.conftest import ADMIN, ANALYST
from tests.fake_smtp import FakeSMTP
from tests.test_integration import (
    CUSTOMERS,
    DB_URL,
    NATS_URL,
    insert_scores,
    reset_database,
)

pytestmark = pytest.mark.integration

EMAIL = {c[0]: c[1] for c in CUSTOMERS}
LEVEL = {c[0]: c[5] for c in CUSTOMERS}


@pytest.fixture
async def env(monkeypatch):
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
    async with FakeSMTP() as smtp:
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(smtp.port))
        monkeypatch.setenv("SMTP_FROM", "care@hosting.test")
        monkeypatch.setattr(settings, "database_url", DB_URL)
        monkeypatch.setattr(settings, "nats_url", NATS_URL)
        monkeypatch.setattr(settings, "mail_rate_per_s", 50.0)
        monkeypatch.setattr(settings, "mail_poll_interval_s", 0.2)
        monkeypatch.setattr(settings, "playbook_poll_interval_s", 0.2)
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await insert_scores(DB_URL)
                yield client, app, smtp


async def _sql(query: str, *args):
    conn = await asyncpg.connect(DB_URL)
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


async def recalculated() -> None:
    """Как scoring после пересчёта: строка summary переписана."""
    await _sql(
        "INSERT INTO scoring.summary (id, data, model_version, scored_at) VALUES (1, '{}', 1, clock_timestamp()) "
        "ON CONFLICT (id) DO UPDATE SET scored_at = clock_timestamp()"
    )


async def wait_for(check, timeout_s: float = 10.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        value = await check()
        if value:
            return value
        if asyncio.get_running_loop().time() > deadline:
            return value
        await asyncio.sleep(0.1)


def body(**kw) -> dict:
    data = {
        "name": "Высокий риск",
        "conditions": {"risk_levels": ["high", "medium"]},
        "actions": [{"type": "email", "template_code": "general"}],
        # при 16% в контроль из клиентов 1, 3, 4, 7 попадают 1 и 7 (точки 0.025 и 0.146)
        "control_share": 0.16,
        "mode": "live",
        "enabled": True,
    }
    data.update(kw)
    return data


def split(ids, share):
    control = {i for i in ids if rules.in_control(i, share)}
    return set(ids) - control, control


async def runs_of(playbook_id: int):
    return await _sql(
        "SELECT * FROM scoring.playbook_runs WHERE playbook_id = $1 ORDER BY id",
        playbook_id,
    )


async def test_recalculation_fires_and_control_gets_nothing(env):
    client, _, smtp = env
    resp = await client.post("/api/v1/playbooks", json=body(), headers=ADMIN)
    assert resp.status_code == 201, resp.text
    pb = resp.json()
    assert pb["created_by"] == "admin" and pb["stats"] == {
        "treated": 0,
        "control": 0,
        "emails": 0,
    }

    treated, control = split([1, 3, 4, 7], 0.16)
    assert (treated, control) == ({3, 4}, {1, 7})
    preview = (
        await client.post("/api/v1/playbooks/preview", json=body(), headers=ANALYST)
    ).json()
    assert preview["matched"] == 4
    assert (preview["treated"], preview["control"]) == (len(treated), len(control))

    await recalculated()
    assert await wait_for(lambda: _count_runs(pb["id"]))
    hits = await _sql(
        "SELECT customer_id, grp, email_id FROM scoring.playbook_hits WHERE playbook_id = $1",
        pb["id"],
    )
    assert {h["customer_id"] for h in hits if h["grp"] == "treated"} == treated
    assert {h["customer_id"] for h in hits if h["grp"] == "control"} == control
    assert all(h["email_id"] is None for h in hits if h["grp"] == "control")

    async def delivered():
        return len(smtp.messages) >= len(treated)

    assert await wait_for(delivered)
    await asyncio.sleep(0.3)
    to = {m["To"] for m in smtp.messages}
    assert to == {EMAIL[i] for i in treated}
    assert not to & {EMAIL[i] for i in control}

    # то же событие второй раз не обрабатывается, а новый пересчёт упирается в «не чаще раза в K дней»
    await asyncio.sleep(0.5)
    assert len(await runs_of(pb["id"])) == 1
    await recalculated()
    await asyncio.sleep(0.8)
    assert (
        len(await runs_of(pb["id"])) == 1
    )  # всех держит пауза — пустой проход журнал не засоряет

    journal = (
        await client.get(f"/api/v1/playbooks/{pb['id']}/hits", headers=ANALYST)
    ).json()["items"]
    assert len(journal) == 4
    treated_row = next(j for j in journal if j["group"] == "treated")
    assert treated_row["actions"] == [{"type": "email", "result": "queued"}]
    listed = (await client.get("/api/v1/playbooks", headers=ANALYST)).json()
    assert listed["mail_enabled"] is True and len(listed["presets"]) == 3
    assert listed["items"][0]["stats"]["treated"] == len(treated)

    # письмо от сценария ставит клиенту статус «письмо отправлено», как и ручное
    some = next(iter(treated))
    state = (
        await client.get(f"/api/v1/customers/{some}/action", headers=ANALYST)
    ).json()
    assert state["action"]["status"] == "emailed"


async def _count_runs(playbook_id: int) -> int:
    return len(await runs_of(playbook_id))


async def test_simulation_sends_nothing(env):
    client, _, smtp = env
    pb = (
        await client.post(
            "/api/v1/playbooks", json=body(mode="simulation"), headers=ADMIN
        )
    ).json()
    await recalculated()
    assert await wait_for(lambda: _count_runs(pb["id"]))
    run = (await runs_of(pb["id"]))[0]
    assert run["mode"] == "simulation" and run["matched"] == 4 and run["emails"] == 0
    assert await _sql("SELECT 1 FROM scoring.playbook_hits") == []
    await asyncio.sleep(0.5)
    assert smtp.messages == []
    assert await _sql("SELECT 1 FROM scoring.customer_emails") == []


async def test_manual_run_limit_status_and_delete(env):
    client, _, smtp = env
    rule = body(
        enabled=False,
        control_share=0.0,
        max_per_day=1,
        actions=[
            {"type": "status", "status": "snoozed", "days": 3},
            {"type": "notify", "username": "admin"},
        ],
    )
    pb = (await client.post("/api/v1/playbooks", json=rule, headers=ADMIN)).json()
    first = (
        await client.post(f"/api/v1/playbooks/{pb['id']}/run", headers=ADMIN)
    ).json()
    assert first["treated"] == 1 and first["skipped_limit"] == 3
    # первым берём самого «дорогого» клиента
    assert first["sample"][0]["customer_id"] in (1, 4)
    cid = first["sample"][0]["customer_id"]
    state = (
        await client.get(f"/api/v1/customers/{cid}/action", headers=ANALYST)
    ).json()
    assert state["action"]["status"] == "snoozed"
    second = (
        await client.post(f"/api/v1/playbooks/{pb['id']}/run", headers=ADMIN)
    ).json()
    assert second["treated"] == 0 and second["skipped_cooldown"] == 1
    assert smtp.messages == []
    bell = await _sql(
        "SELECT title FROM alerts.notifications WHERE username = 'admin' AND event_type = 'playbook'"
    )
    assert any("Высокий риск" in b["title"] for b in bell)

    runs = (
        await client.get(f"/api/v1/playbooks/{pb['id']}/runs", headers=ANALYST)
    ).json()["items"]
    assert [r["cause"] for r in runs] == ["manual", "manual"] and runs[0][
        "created_by"
    ] == "admin"
    assert (
        await client.delete(f"/api/v1/playbooks/{pb['id']}", headers=ADMIN)
    ).status_code == 204
    assert (
        await client.get(f"/api/v1/playbooks/{pb['id']}", headers=ANALYST)
    ).status_code == 404
    assert await _sql("SELECT 1 FROM scoring.playbook_hits") == []


async def test_became_high_after_baseline(env):
    client, _, smtp = env
    rule = body(conditions={"became_high": True}, control_share=0.0)
    pb = (await client.post("/api/v1/playbooks", json=rule, headers=ADMIN)).json()
    await recalculated()  # первый проход только запоминает уровни
    assert await wait_for(lambda: _levels_ready())
    assert await runs_of(pb["id"]) == []

    await _sql(
        "UPDATE scoring.customer_scores SET risk_level = 'high', risk_score = 70 WHERE customer_id = 3"
    )
    await recalculated()
    assert await wait_for(lambda: _count_runs(pb["id"]))
    hits = await _sql(
        "SELECT customer_id FROM scoring.playbook_hits WHERE playbook_id = $1", pb["id"]
    )
    assert [h["customer_id"] for h in hits] == [3]

    async def delivered():
        return smtp.messages

    assert [m["To"] for m in await wait_for(delivered)] == [EMAIL[3]]


async def _levels_ready() -> bool:
    rows = await _sql("SELECT levels_ready FROM scoring.playbook_state")
    return rows[0]["levels_ready"]


async def test_uplift_on_synthetic_hits(env):
    """Срабатывания 10 дней назад: у затронутых риск снизился чаще, чем в контроле."""
    client, _, _ = env
    pb = (
        await client.post("/api/v1/playbooks", json=body(enabled=False), headers=ADMIN)
    ).json()
    await _sql(
        """
        INSERT INTO scoring.customer_scores (customer_id, email, balance, bonus_balance, bonus_state, withdraw_balance,
            total_replenished, risk_score, risk_level, reasons, services_total, services_at_risk, monthly_revenue,
            expected_loss, exposure, detail, model_version, scored_at)
        SELECT 1000 + g, 'syn' || g || '@example.test', 0, 0, 'locked', 0, 1000,
               CASE WHEN g <= 100 AND g % 10 < 7 THEN 40 WHEN g > 100 AND g % 10 < 3 THEN 40 ELSE 80 END,
               'high', '[]', 1, 1, 1000, 100, 100, '{}', 1, now()
        FROM generate_series(1, 160) g
        """
    )
    run_id = (
        await _sql(
            "INSERT INTO scoring.playbook_runs (playbook_id, cause, mode, matched, treated, control) "
            "VALUES ($1, 'manual', 'live', 160, 100, 60) RETURNING id",
            pb["id"],
        )
    )[0]["id"]
    await _sql(
        """
        INSERT INTO scoring.playbook_hits (playbook_id, run_id, customer_id, grp, risk_level_at, risk_score_at,
            expected_loss_at, balance_at, replenished_at, hit_at)
        SELECT $1, $2, 1000 + g, CASE WHEN g <= 100 THEN 'treated' ELSE 'control' END, 'high', 80, 100, 0, 1000,
               now() - interval '10 days'
        FROM generate_series(1, 160) g
        """,
        pb["id"],
        run_id,
    )
    data = (
        await client.get(f"/api/v1/playbooks/{pb['id']}/uplift", headers=ANALYST)
    ).json()
    by_days = {w["days"]: w for w in data["windows"]}
    week = by_days[7]
    assert (week["treated_n"], week["control_n"]) == (100, 60) and week["enough"]
    risk = next(m for m in week["metrics"] if m["code"] == "risk_down")
    assert risk["treated_share"] == 0.7 and risk["control_share"] == 0.3
    assert risk["ci_low"] > 0 and risk["verdict"] == "better"
    assert by_days[14]["treated_n"] == 0 and "ещё нет" in by_days[14]["summary"]
