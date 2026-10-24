"""Графики и фильтр по причине на настоящем Postgres: миграция 009, SQL, контракт ответов."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from tests.conftest import ANALYST, MARKETER
from tests.test_integration import DB_URL, SCORED_AT, client  # noqa: F401  (фикстура client)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE = datetime(2026, 10, 19, 15, tzinfo=UTC)

# (id, email, score, level, expected_loss, monthly_revenue, [(code, contribution)], главная причина без оценки)
CUSTOMERS = [
    (1, "anna@example.test", 80.0, "high", 4_000, 5_000, [("prolong_wait", 60.0), ("inactivity", 20.0)], None),
    (2, "boris@example.test", 40.0, "medium", 1_000, 2_500, [("inactivity", 30.0), ("prolong_wait", 10.0)], None),
    (3, "clara@example.test", 35.0, "medium", 700, 2_000, [("short_commitment", 35.0)], None),
    (4, "dmitry@example.test", 100.0, "high", 3_000, 3_000, [("prolong_wait", 100.0)], None),
    (5, "eva@example.test", 5.0, "low", 50, 1_000, [("inactivity", 5.0)], None),
    (6, "fedor@example.test", None, "unknown", 0, 0, [], "all_services_gone"),
]


async def seed(url: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        for cid, email, score, level, loss, mrr, features, unknown in CUSTOMERS:
            if unknown:
                reasons = [{"code": unknown, "title": "Все услуги удалены", "label": "Уже ушёл", "contribution": 0.0}]
            else:
                reasons = [
                    {"code": code, "title": f"{code} у клиента {cid}", "contribution": pts,
                     "action": f"Написать клиенту: vps-{cid} ждёт оплаты" if code == "prolong_wait" else "Позвонить"}
                    for code, pts in features
                ]
            detail = {
                "services": [],
                "features": [{"code": code, "title": code, "contribution": pts} for code, pts in features],
                "money": {"monthly_revenue": mrr, "exposure": loss, "expected_loss": loss,
                          "funds_run_out_at": None, "explanation": ""},
            }
            await conn.execute(
                """
                INSERT INTO scoring.customer_scores (customer_id, email, balance, bonus_balance, bonus_state,
                    withdraw_balance, total_replenished, risk_score, risk_level, loyalty, reasons,
                    services_total, services_at_risk, monthly_revenue, expected_loss, exposure, detail,
                    model_version, scored_at)
                VALUES ($1,$2,1000,200,'unlocked',0,5000,$3,$4,NULL,$5::jsonb,1,0,$6,$7,$7,$8::jsonb,2,$9)
                """,
                cid, email, score, level, json.dumps(reasons), mrr, loss, json.dumps(detail), SCORED_AT,
            )
        await conn.execute(
            "INSERT INTO scoring.summary (id, data, model_version, scored_at) VALUES (1, $1::jsonb, 2, $2)",
            json.dumps({"horizon_days": 30}), SCORED_AT,
        )
        await conn.execute("UPDATE source.clock SET reference_time = $1", REFERENCE)
        # у клиента 1: недельная услуга с автопродлением и платежи за два месяца
        await conn.execute(
            "INSERT INTO source.accounts (id, email, balance, withdraw_balance, total_replenished, bonus_balance, "
            "bonus_state) VALUES (1, 'anna@example.test', 1000, 0, 5000, 200, 'unlocked')"
        )
        await conn.execute(
            "INSERT INTO source.services (id, name, owner_id, payment_term, auto_prolong, created_at, expires_at, "
            "status, price) VALUES (10, 'vps-10', 1, 'week', true, $1, $2, 'active', 500)",
            REFERENCE - timedelta(days=120), REFERENCE + timedelta(days=1),
        )
        for tid, amount, status, at in (
            (100, 500, "performed", datetime(2026, 9, 3, tzinfo=UTC)),
            (101, 500, "performed", datetime(2026, 9, 10, tzinfo=UTC)),
            (102, 500, "not_performed", datetime(2026, 10, 1, tzinfo=UTC)),
            (103, 700, "performed", datetime(2025, 1, 1, tzinfo=UTC)),  # старше года — в график не идёт
        ):
            await conn.execute(
                "INSERT INTO source.transactions (id, service_id, amount, status, type, created_at, performed_at) "
                "VALUES ($1, 10, $2, $3, 'prolong', $4, $5)",
                tid, amount, status, at, at if status == "performed" else None,
            )
        await conn.execute(
            "INSERT INTO scoring.customer_emails (customer_id, to_email, subject, body, status, "
            "risk_score_at_send, sent_at) VALUES (1, 'anna@example.test', 's', 'b', 'sent', 91.5, $1)",
            REFERENCE - timedelta(days=3),
        )
        for i in range(3):
            await conn.execute(
                "INSERT INTO scoring.summary_history (scored_at, model_version, full_run, customers_total, high, "
                "medium, low, unknown, services_at_risk, expected_loss, exposure, monthly_revenue_total, "
                "revenue_at_risk_share) VALUES ($1, 2, $2, 6, $3, 2, 1, 1, 4, $4, 9000, 13500, 0.3)",
                REFERENCE + timedelta(minutes=i), i == 0, 2 + i, 8000 + i * 100,
            )
    finally:
        await conn.close()


@pytest.fixture
def contract():
    """Проверка ответа схемой из contracts/openapi.yaml (если jsonschema есть в окружении)."""
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    import yaml

    spec = yaml.safe_load((REPO_ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    uri = "urn:churnradar:openapi"
    registry = referencing.Registry().with_resource(
        uri, referencing.Resource.from_contents(spec, default_specification=referencing.jsonschema.DRAFT202012)
    )

    def check(name: str, instance: object) -> None:
        schema = {"$ref": f"{uri}#/components/schemas/{name}"}
        jsonschema.Draft202012Validator(schema, registry=registry).validate(instance)

    return check


async def ids(client, **params) -> list[int]:  # noqa: F811
    resp = await client.get("/api/v1/customers", params={"page_size": 200, **params}, headers=ANALYST)
    assert resp.status_code == 200, resp.text
    return sorted(i["id"] for i in resp.json()["items"])


async def test_filter_by_reason_and_score(client):  # noqa: F811
    await seed(DB_URL)
    assert await ids(client, reason="prolong_wait") == [1, 2, 4]
    assert await ids(client, reason="prolong_wait", reason_scope="main") == [1, 4]
    assert await ids(client, reason="inactivity", reason_scope="main", level="medium") == [2]
    # причина «нет оценки» живёт только в reasons, фильтр её тоже видит
    assert await ids(client, reason="all_services_gone") == [6]
    assert await ids(client, score_min=30, score_max=40) == [3]
    assert await ids(client, score_min=90, score_max=100) == [4]  # ровно 100 входит в последнюю корзину
    assert await ids(client, score_min=0, score_max=10) == [5]
    csv = await client.get(
        "/api/v1/customers/export.csv", params={"reason": "prolong_wait", "reason_scope": "main"}, headers=ANALYST
    )
    assert csv.status_code == 200
    lines = csv.content.decode("utf-8-sig").strip().splitlines()
    assert len(lines) == 3  # заголовок и два клиента


async def test_distribution(client, contract):  # noqa: F811
    await seed(DB_URL)
    body = (await client.get("/api/v1/analytics/distribution", headers=ANALYST)).json()
    contract("RiskDistribution", body)
    counts = [b["customers"] for b in body["buckets"]]
    assert counts == [1, 0, 0, 1, 1, 0, 0, 0, 1, 1]
    assert body["buckets"][9]["by_level"]["high"] == 1  # score 100 — в последней корзине
    assert sum(counts) == 5  # без оценки в корзины не попадает
    levels = {lvl["level"]: lvl for lvl in body["levels"]}
    assert levels["high"]["monthly_revenue"] == 8_000 and levels["high"]["expected_loss"] == 7_000
    assert levels["unknown"]["customers"] == 1


async def test_scatter(client, contract):  # noqa: F811
    await seed(DB_URL)
    body = (await client.get("/api/v1/analytics/scatter", params={"limit": 2}, headers=ANALYST)).json()
    contract("RiskScatter", body)
    assert body["total"] == 5 and body["shown"] == 2
    assert [p["id"] for p in body["points"]] == [1, 4]  # самые дорогие
    filtered = (
        await client.get("/api/v1/analytics/scatter", params={"reason": "inactivity"}, headers=ANALYST)
    ).json()
    assert sorted(p["id"] for p in filtered["points"]) == [1, 2, 5]
    ranged = (
        await client.get("/api/v1/analytics/scatter", params={"score_min": 30, "score_max": 50}, headers=ANALYST)
    ).json()
    assert sorted(p["id"] for p in ranged["points"]) == [2, 3]


async def test_reason_detail(client, contract):  # noqa: F811
    await seed(DB_URL)
    body = (await client.get("/api/v1/analytics/reasons/prolong_wait", headers=ANALYST)).json()
    contract("ReasonDetail", body)
    assert body["title"] == "Ждёт оплаты" and body["plain"]
    assert body["customers"] == 3 and body["primary_customers"] == 2
    # потеря на причину: 4000·60/80 + 1000·10/40 + 3000·100/100
    assert body["expected_loss"] == 3_000 + 250 + 3_000
    assert body["customers_expected_loss"] == 8_000
    assert body["by_level"] == {"high": 2, "medium": 1, "low": 0, "unknown": 0}
    buckets = {(b["from"], b["to"]): b["customers"] for b in body["contribution"]}
    assert buckets[(5, 10)] == 0 and buckets[(10, 20)] == 1 and buckets[(50, 100)] == 2
    assert body["actions"][0] == {"title": "Написать клиенту", "customers": 3}
    assert [c["id"] for c in body["top_customers"]] == [1, 4, 2]
    main = (await client.get("/api/v1/analytics/reasons/prolong_wait?scope=main", headers=ANALYST)).json()
    assert main["customers"] == 2
    gone = (await client.get("/api/v1/analytics/reasons/all_services_gone", headers=ANALYST)).json()
    contract("ReasonDetail", gone)
    assert gone["customers"] == 1 and gone["actions"][0]["title"] == "Предложить вернуться"
    missing = await client.get("/api/v1/analytics/reasons/no_such_reason", headers=ANALYST)
    assert missing.status_code == 404


async def test_customer_charts(client, contract):  # noqa: F811
    await seed(DB_URL)
    body = (await client.get("/api/v1/customers/1/charts", headers=ANALYST)).json()
    contract("CustomerCharts", body)
    runway = body["runway"]
    assert runway["start_balance"] == 1_200  # баланс и разблокированные бонусы
    assert runway["days"] == 30 and len(runway["points"]) == 31
    # 500 в неделю: 20.10, 27.10 — оплачены, 03.11 — денег уже нет
    assert runway["runs_out_at"] == "2026-11-03T15:00:00Z"
    assert runway["points"][1]["paid"] == 500 and runway["points"][-1]["balance"] == 200
    months = {m["month"]: m for m in body["payments"]}
    assert len(body["payments"]) == 12 and "2025-01" not in months
    assert months["2026-09"]["paid"] == 1_000 and months["2026-09"]["payments"] == 2
    assert months["2026-10"]["failed"] == 1
    assert [p["source"] for p in body["score_history"]] == ["email", "now"]
    assert body["score_history"][0]["risk_score"] == 91.5
    # без писем истории score нет — не выдумываем
    other = (await client.get("/api/v1/customers/2/charts", headers=ANALYST)).json()
    assert other["score_history"] == [] and other["runway"]["renewals"] == 0
    assert (await client.get("/api/v1/customers/999/charts", headers=ANALYST)).status_code == 404


async def test_history(client, contract):  # noqa: F811
    await seed(DB_URL)
    body = (await client.get("/api/v1/analytics/history", headers=ANALYST)).json()
    contract("SummaryHistory", body)
    assert [i["expected_loss"] for i in body["items"]] == [8_000, 8_100, 8_200]
    assert body["items"][0]["full"] and not body["items"][1]["full"]
    last = (await client.get("/api/v1/analytics/history?limit=1", headers=ANALYST)).json()
    assert [i["expected_loss"] for i in last["items"]] == [8_200]


async def test_campaign_preview_by_reason(client):  # noqa: F811
    await seed(DB_URL)
    resp = await client.post(
        "/api/v1/mail/campaigns/preview",
        json={"reason": "prolong_wait", "reason_scope": "main", "skip_days": 0},
        headers=MARKETER,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2 and body["to_send"] == 2
    assert body["sample"]["customer_id"] == 1  # самые дорогие первыми
    bad = await client.post(
        "/api/v1/mail/campaigns/preview", json={"reason": "prolong_wait", "worklist": True}, headers=MARKETER
    )
    assert bad.status_code == 422
