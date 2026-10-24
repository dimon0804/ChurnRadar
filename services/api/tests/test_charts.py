"""Графики и фильтр по причине: SQL-условия, сборка рядов, валидация параметров."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from riskcore.types import Service

from app import charts, queries
from app.mailer import CampaignRequest
from tests.conftest import ANALYST, FakePool

NOW = datetime(2026, 10, 19, 15, tzinfo=UTC)


def test_where_clause_reason_scopes():
    sql, args = queries.where_clause([], None, reason="prolong_wait", reason_scope="main")
    assert sql == " WHERE main_reason = $1" and args == ["prolong_wait"]
    sql, args = queries.where_clause(["high"], None, reason="prolong_wait")
    assert sql == " WHERE risk_level = ANY($1::text[]) AND reason_codes ? $2"
    assert args == [["high"], "prolong_wait"]


def test_where_clause_score_range_upper_bound():
    sql, args = queries.where_clause([], None, score_min=30, score_max=40)
    assert sql == " WHERE risk_score >= $1 AND risk_score < $2" and args == [30.0, 40.0]
    # последняя корзина включает 100: иначе клиент со score ровно 100 не попал бы никуда
    sql, _ = queries.where_clause([], None, score_min=90, score_max=100)
    assert "risk_score <= $2" in sql


async def test_customers_rejects_bad_filters(client):
    resp = await client.get("/api/v1/customers", params={"score_min": 50, "score_max": 40}, headers=ANALYST)
    assert resp.status_code == 422
    resp = await client.get("/api/v1/customers", params={"reason": "DROP TABLE"}, headers=ANALYST)
    assert resp.status_code == 422
    resp = await client.get("/api/v1/customers", params={"reason_scope": "all"}, headers=ANALYST)
    assert resp.status_code == 422
    resp = await client.get("/api/v1/customers", params={"score_max": 101}, headers=ANALYST)
    assert resp.status_code == 422


async def test_customers_passes_reason_to_sql(client, app):
    app.state.pool = FakePool({"count(*)": 2, "ORDER BY": [], "COALESCE((SELECT model_version": {
        "model_version": 3, "scored_at": NOW}})
    resp = await client.get(
        "/api/v1/customers", params={"reason": "inactivity", "reason_scope": "main"}, headers=ANALYST
    )
    assert resp.status_code == 200 and resp.json()["total"] == 2
    assert any("main_reason = $1" in q for q in app.state.pool.conn.queries)


def test_build_distribution_splits_levels_inside_bucket():
    levels = [
        {"risk_level": "high", "customers": 2, "monthly_revenue": 5000, "expected_loss": 3000},
        {"risk_level": "unknown", "customers": 1, "monthly_revenue": 0, "expected_loss": 0},
    ]
    buckets = [
        {"bucket": 3, "risk_level": "low", "customers": 4, "expected_loss": 10},
        {"bucket": 3, "risk_level": "medium", "customers": 1, "expected_loss": 20},
        {"bucket": 9, "risk_level": "high", "customers": 2, "expected_loss": 3000},
    ]
    out = charts.build_distribution(levels, buckets)
    assert len(out["buckets"]) == 10
    third = out["buckets"][3]
    assert (third["from"], third["to"], third["customers"]) == (30, 40, 5)
    assert third["by_level"] == {"high": 0, "medium": 1, "low": 4}
    assert out["buckets"][0]["customers"] == 0
    assert [lvl["level"] for lvl in out["levels"]] == ["high", "medium", "low", "unknown"]
    assert out["levels"][1] == {"level": "medium", "customers": 0, "monthly_revenue": 0, "expected_loss": 0}


async def test_distribution_endpoint(client, app):
    app.state.pool = FakePool(
        {
            "GROUP BY risk_level": [{"risk_level": "low", "customers": 1, "monthly_revenue": 100, "expected_loss": 5}],
            "GROUP BY 1, 2": [{"bucket": 0, "risk_level": "low", "customers": 1, "expected_loss": 5}],
            "FROM scoring.summary": {"scored_at": NOW, "model_version": 4},
        }
    )
    body = (await client.get("/api/v1/analytics/distribution", headers=ANALYST)).json()
    assert body["buckets"][0]["by_level"]["low"] == 1
    assert body["model_version"] == 4 and body["scored_at"] == "2026-10-19T15:00:00Z"


def test_contribution_buckets_cover_scale():
    out = charts.contribution_buckets([{"bucket": 0, "customers": 3}, {"bucket": 5, "customers": 1}])
    assert [(b["from"], b["to"]) for b in out] == [(0, 5), (5, 10), (10, 20), (20, 30), (30, 50), (50, 100)]
    assert out[0]["customers"] == 3 and out[-1]["customers"] == 1


def test_reason_info_for_unknown_reason_and_missing_feature(monkeypatch):
    info = charts.reason_info("all_services_gone")
    assert info is not None and info["title"] == "Уже ушёл" and info["default_action"]
    assert charts.reason_info("no_such_reason") is None


def test_reason_info_uses_plain_words():
    info = charts.reason_info("prolong_wait")
    assert info is not None and info["feature"]
    assert info["plain"] == charts.PLAIN["prolong_wait"]
    assert info["description"]


def test_every_feature_has_plain_words():
    import riskcore

    codes = {spec.code for spec in riskcore.feature_catalog()}
    assert codes <= set(charts.PLAIN), f"нет простого описания: {codes - set(charts.PLAIN)}"


async def test_reason_404_when_unknown_and_empty(client, app):
    app.state.pool = FakePool({"count(*) AS customers": {"customers": 0}})
    resp = await client.get("/api/v1/analytics/reasons/no_such_reason", headers=ANALYST)
    assert resp.status_code == 404
    resp = await client.get("/api/v1/analytics/reasons/Bad-Code", headers=ANALYST)
    assert resp.status_code == 422


def test_payments_by_month_fills_gaps():
    rows = [{"month": datetime(2026, 9, 1, tzinfo=UTC), "paid": 1500, "payments": 2, "failed": 1}]
    out = charts.payments_by_month(rows, NOW, months=3)
    assert [m["month"] for m in out] == ["2026-08", "2026-09", "2026-10"]
    assert out[1] == {"month": "2026-09", "paid": 1500, "payments": 2, "failed": 1}
    assert out[0]["paid"] == 0 and out[2]["payments"] == 0


def test_months_back_crosses_year():
    assert [d.isoformat() for d in charts._months_back(datetime(2026, 2, 3, tzinfo=UTC), 3)] == [
        "2025-12-01", "2026-01-01", "2026-02-01"
    ]


def _service(sid: int, price: int, term: str, expires: datetime, auto: bool = True) -> Service:
    return Service(sid, f"s{sid}", 1, term, auto, NOW - timedelta(days=90), expires, "active", price)


def test_runway_runs_out_on_first_unpaid_renewal():
    services = [_service(1, 400, "week", NOW + timedelta(days=2)), _service(2, 999, "month", NOW, auto=False)]
    runway = charts.build_runway(services, spendable=1000, as_of=NOW, days=30)
    points = runway["points"]
    assert len(points) == 31 and points[0]["date"] == "2026-10-19"
    # 400 списывается на 2-й и 9-й день, на 16-й денег уже нет
    assert points[2]["paid"] == 400 and points[2]["balance"] == 600
    assert points[9]["balance"] == 200
    assert points[16]["unpaid"] == 400 and points[16]["balance"] == 200
    assert runway["runs_out_at"] == "2026-11-04T15:00:00Z"
    assert runway["renewals"] == 5  # выключенное автопродление в график не попадает
    assert min(p["balance"] for p in points) >= 0


def test_runway_without_renewals_is_flat():
    runway = charts.build_runway([_service(1, 500, "month", NOW, auto=False)], 700, NOW, 30)
    assert runway["runs_out_at"] is None and runway["renewals"] == 0
    assert {p["balance"] for p in runway["points"]} == {700}


def test_runway_window():
    assert charts.runway_window(NOW, None, 30) == 30
    assert charts.runway_window(NOW, NOW + timedelta(days=50, hours=3), 30) == 58
    assert charts.runway_window(NOW, NOW + timedelta(days=900), 30) == charts.RUNWAY_LIMIT_DAYS


def test_campaign_request_by_reason():
    req = CampaignRequest(reason="prolong_wait", reason_scope="main")
    assert req.reason == "prolong_wait" and not req.worklist
    with pytest.raises(ValidationError):
        CampaignRequest(reason="prolong_wait", worklist=True)
    with pytest.raises(ValidationError):
        CampaignRequest(reason="Bad Code")
    with pytest.raises(ValidationError):
        CampaignRequest()


async def test_history_endpoint(client, app):
    app.state.pool = FakePool(
        {
            "summary_history": [
                {
                    "scored_at": NOW, "model_version": 2, "full_run": True, "customers_total": 10,
                    "high": 3, "medium": 2, "low": 4, "unknown": 1, "services_at_risk": 7,
                    "expected_loss": 1234, "exposure": 5000, "monthly_revenue_total": 9000,
                    "revenue_at_risk_share": 0.25,
                }
            ]
        }
    )
    body = (await client.get("/api/v1/analytics/history", params={"limit": 10}, headers=ANALYST)).json()
    item = body["items"][0]
    assert item["at"] == "2026-10-19T15:00:00Z" and item["full"] is True
    assert item["by_level"] == {"high": 3, "medium": 2, "low": 4, "unknown": 1}
    resp = await client.get("/api/v1/analytics/history", params={"limit": 501}, headers=ANALYST)
    assert resp.status_code == 422
