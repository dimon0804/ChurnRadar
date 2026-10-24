"""Маркетинговые метрики в api без БД: права, валидация настроек, маржа и CAC поверх базы, /metrics."""

from __future__ import annotations

import pytest
import riskcore

from app import marketing
from tests.conftest import ADMIN, ANALYST, NOW, FakePool

TOKEN = {"Authorization": "Bearer churn-metrics-token"}


def _base() -> dict:
    """База как из scoring: MRR €200 у трёх клиентов, отток 25% в месяц."""
    base = riskcore.build_marketing([], [], 30, NOW)
    base.update(
        {
            "customers_total": 4, "active_customers": 3, "paying_customers": 3,
            "mrr": 20_000, "arpu": 6_667, "arppu": 6_667, "ltv": 26_668, "ltv_total": 80_000, "ltv_at_risk": 50_000,
        }
    )
    base["churn"] = {**base["churn"], "customer_monthly": 0.25, "revenue_monthly": 0.3}
    base["segments"] = [
        {"level": "high", "customers": 1, "mrr": 10_000, "arpu": 10_000, "monthly_churn": 0.5,
         "ltv": 20_000, "ltv_avg": 20_000, "expected_mrr_loss": 5_000},
    ]
    return base


def _summary_row(base: dict | None) -> dict:
    data = {"customers_total": 4, "monthly_revenue_total": 20_000, "horizon_days": 30}
    if base is not None:
        data["marketing"] = base
    return {"data": data, "model_version": 5, "scored_at": NOW}


def _settings_row(margin: float = 1.0, cac: int | None = None) -> dict:
    return {"margin": margin, "cac": cac, "updated_at": NOW, "updated_by": "admin"}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/analytics/marketing", None),
        ("GET", "/api/v1/analytics/marketing/settings", None),
        ("PUT", "/api/v1/analytics/marketing/settings", {"margin": 0.5}),
    ],
)
async def test_requires_user(client, method, path, body):
    resp = await client.request(method, path, json=body)
    assert resp.status_code == 401


async def test_settings_admin_only(client):
    resp = await client.put("/api/v1/analytics/marketing/settings", json={"margin": 0.5}, headers=ANALYST)
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"margin": 0},
        {"margin": 1.5},
        {"margin": 0.5, "cac": 0},
        {"margin": 0.5, "cac": -100},
        {"margin": 0.5, "cac": 10**12},
        {"margin": 0.5, "extra": 1},
        {"cac": 1000},
    ],
)
async def test_settings_validation(client, body):
    resp = await client.put("/api/v1/analytics/marketing/settings", json=body, headers=ADMIN)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"


async def test_settings_put_returns_new_state(client, app):
    app.state.pool = FakePool({"INSERT INTO scoring.marketing_settings": _settings_row(0.4, 2_500)})
    resp = await client.put("/api/v1/analytics/marketing/settings", json={"margin": 0.4, "cac": 2_500}, headers=ADMIN)
    assert resp.status_code == 200
    assert resp.json() == {"margin": 0.4, "cac": 2_500, "updated_at": "2026-10-19T15:00:00Z", "updated_by": "admin"}


async def test_settings_default_when_not_set(client, app):
    app.state.pool = FakePool({"FROM scoring.marketing_settings": None})
    body = (await client.get("/api/v1/analytics/marketing/settings", headers=ANALYST)).json()
    assert body == {"margin": 1.0, "cac": None, "updated_at": None, "updated_by": None}


async def test_marketing_applies_margin_and_cac(client, app):
    app.state.pool = FakePool(
        {"FROM scoring.summary": _summary_row(_base()), "FROM scoring.marketing_settings": _settings_row(0.5, 4_000)}
    )
    body = (await client.get("/api/v1/analytics/marketing", headers=ANALYST)).json()
    assert body["mrr"] == 20_000 and body["arpu"] == 6_667
    assert body["ltv"] == 13_334  # €266,68 × 50%
    assert body["ltv_total"] == 40_000 and body["ltv_at_risk"] == 25_000
    assert body["segments"][0]["ltv"] == 10_000
    economics = body["unit_economics"]
    assert economics["margin"] == 0.5 and economics["cac"] == 4_000
    assert economics["ltv_cac"] == pytest.approx(13_334 / 4_000)
    assert economics["payback_months"] == pytest.approx(4_000 / (6_667 * 0.5))
    assert body["settings"]["margin"] == 0.5
    assert body["model_version"] == 5 and body["scored_at"] == "2026-10-19T15:00:00Z"


async def test_marketing_without_cac_has_no_ratio(client, app):
    app.state.pool = FakePool({"FROM scoring.summary": _summary_row(_base()), "FROM scoring.marketing_settings": None})
    body = (await client.get("/api/v1/analytics/marketing", headers=ANALYST)).json()
    assert body["ltv"] == 26_668
    assert body["unit_economics"] == {"margin": 1.0, "cac": None, "ltv_cac": None, "payback_months": None}


async def test_marketing_before_first_rescore(client, app):
    """Сводки нет или она старого формата — все поля на месте, нули и null."""
    app.state.pool = FakePool(
        {"FROM scoring.summary": _summary_row(None), "FROM scoring.marketing_settings": None}
    )
    body = (await client.get("/api/v1/analytics/marketing", headers=ANALYST)).json()
    assert body["mrr"] == 0 and body["ltv"] == 0
    assert body["churn"]["customer_monthly"] is None
    assert len(body["segments"]) == 4


async def test_summary_does_not_leak_marketing(client, app):
    app.state.pool = FakePool({"FROM scoring.summary": _summary_row(_base())})
    body = (await client.get("/api/v1/analytics/summary", headers=ANALYST)).json()
    assert "marketing" not in body
    assert body["customers_total"] == 4


async def test_metrics_match_marketing_page(client, app):
    app.state.pool = FakePool(
        {"FROM scoring.summary": _summary_row(_base()), "FROM scoring.marketing_settings": _settings_row(0.5)}
    )
    text = (await client.get("/metrics", headers=TOKEN)).text
    assert "churn_mrr_eur 200.0" in text
    assert "churn_arpu_eur 66.67" in text
    assert "churn_ltv_eur 133.34" in text  # уже с маржой, как на странице
    assert "churn_customer_churn_rate 0.25" in text
    assert "churn_revenue_churn_rate 0.3" in text


async def test_customer_card_scales_ltv_by_margin(client, app):
    block = {"mrr": 1_000, "monthly_churn": 0.1, "expected_lifetime_months": 10.0, "lifetime_capped": False,
             "ltv_forecast": 10_000, "ltv_fact": 3_000, "payments": 3, "avg_check": 1_000,
             "first_seen": "2026-01-01T00:00:00Z", "tenure_days": 291.6, "left_at": None}
    row = {
        "customer_id": 7, "email": "a@b.c", "balance": 0, "bonus_balance": 0, "bonus_state": "locked",
        "withdraw_balance": 0, "total_replenished": 100, "risk_score": 10.0, "risk_level": "low",
        "loyalty": 90.0, "reasons": [], "services_total": 1, "services_at_risk": 0,
        "monthly_revenue": 1_000, "expected_loss": 100, "exposure": 0,
        "detail": {"marketing": block}, "model_version": 3, "scored_at": NOW, "margin": 0.3,
    }
    app.state.pool = FakePool({"FROM scoring.customer_scores": row})
    body = (await client.get("/api/v1/customers/7", headers=ANALYST)).json()
    assert body["marketing"]["ltv_forecast"] == 3_000
    assert body["marketing"]["ltv_fact"] == 3_000
    assert body["marketing"]["margin"] == 0.3

    row["margin"] = None  # настроек ещё нет — маржа 100%
    body = (await client.get("/api/v1/customers/7", headers=ANALYST)).json()
    assert body["marketing"]["ltv_forecast"] == 10_000

    row["detail"] = {}  # оценка посчитана до появления блока
    assert (await client.get("/api/v1/customers/7", headers=ANALYST)).json()["marketing"] is None


def test_update_model_bounds():
    assert marketing.MarketingSettingsUpdate(margin=1).cac is None
    assert marketing.MarketingSettingsUpdate(margin=0.01, cac=1).cac == 1
