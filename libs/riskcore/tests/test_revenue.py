"""Выручка за горизонт: по цене, но не больше, чем клиент способен заплатить."""

from __future__ import annotations

from datetime import timedelta

import pytest

from riskcore import merge_settings, score_customers
from riskcore.money import (
    collectible_revenue,
    payment_capacity,
    renewal_plan,
    replenishment_rate,
)

from .factories import AS_OF, account_ctx, make_account, make_service, make_tx, service_ctx


def test_replenishment_rate_is_monthly_average():
    assert replenishment_rate(120_000, 360) == pytest.approx(10_000)
    # Клиент младше месяца: всё, что он внёс, считаем месячным пополнением.
    assert replenishment_rate(50_000, 3) == pytest.approx(50_000)
    assert replenishment_rate(-10, 90) == 0.0


def test_payment_capacity_adds_balance_and_top_ups():
    assert payment_capacity(5_000, 10_000, 30) == pytest.approx(15_000)
    assert payment_capacity(5_000, 10_000, 90) == pytest.approx(35_000)
    assert payment_capacity(-100, 0, 30) == 0.0


def test_collectible_revenue_is_capped_but_keeps_one_payment():
    assert collectible_revenue(68_400, 20_000, 95) == pytest.approx(20_000)
    assert collectible_revenue(1_000, 20_000, 1_000) == pytest.approx(1_000)
    assert collectible_revenue(68_400, 0, 95) == pytest.approx(95)


def test_lifetime_comes_from_earliest_service_or_transaction():
    account = make_account(total_replenished=120_000)
    ctx = account_ctx(
        account, services=[make_service(created_days_ago=30)], transactions=[make_tx(1, days_ago=360)]
    )
    assert ctx.replenishment_rate == pytest.approx(10_000)
    assert ctx.payment_capacity == pytest.approx(account.spendable + 10_000)


def test_hourly_price_above_means_is_capped():
    # €0,95 в час — это €684 в месяц, а клиент за год внёс всего €1 200 и держит €50.
    account = make_account(balance=5_000, total_replenished=120_000)
    hourly = make_service(id=1, payment_term="hour", price=95, created_days_ago=360, expires_in_days=0.02)
    ctx = service_ctx(hourly, account)
    assert ctx.contract_revenue == pytest.approx(95 * 720)
    assert ctx.horizon_revenue == pytest.approx(5_000 + 10_000)
    assert ctx.revenue_capped


def test_regular_service_is_not_capped():
    account = make_account(balance=5_000, total_replenished=120_000)
    ctx = service_ctx(make_service(price=1_000, created_days_ago=360), account)
    assert ctx.horizon_revenue == pytest.approx(ctx.contract_revenue)
    assert not ctx.revenue_capped


def test_capped_revenue_drives_weights_and_money():
    account = make_account(balance=5_000, total_replenished=120_000)
    services = [
        make_service(
            id=1,
            payment_term="hour",
            price=95,
            auto_prolong=False,
            created_days_ago=360,
            expires_in_days=0.02,
        ),
        make_service(id=2, payment_term="year", price=12_000, status="suspended", expires_in_days=200),
    ]
    [result] = score_customers([account], services, [], merge_settings(None), AS_OF)
    by_id = {s["id"]: s for s in result.detail["services"]}
    assert by_id[1]["horizon_revenue"] == 15_000
    assert by_id[1]["monthly_price"] == 95 * 720
    assert by_id[1]["revenue_note"] and "€684" in by_id[1]["revenue_note"]
    assert by_id[2]["revenue_note"] is None
    live = [o for o in result.services if o.probability is not None]
    total = sum(o.revenue for o in live)
    assert total == pytest.approx(15_000 + 1_000)
    assert result.risk_score == pytest.approx(100 * sum(o.probability * o.revenue for o in live) / total)
    assert result.monthly_revenue == round(total)
    assert result.detail["money"]["horizon_revenue"] == round(total)
    assert "1 усл." in result.detail["money"]["explanation"]


def test_next_after_and_cost_before():
    plan = renewal_plan(make_service(payment_term="hour", price=10, expires_in_days=1 / 24), AS_OF, 1)
    assert plan is not None
    assert plan.next_after(AS_OF) == plan.first
    assert plan.next_after(plan.first) == plan.first + timedelta(hours=1)
    assert plan.next_after(plan.end) is None
    assert plan.cost_before(plan.first) == 0
    assert plan.cost_before(plan.first + timedelta(hours=2)) == 20

    monthly = renewal_plan(make_service(price=500, expires_in_days=2), AS_OF, 90)
    assert monthly is not None
    assert monthly.next_after(monthly.first + timedelta(days=1)) is not None
    assert monthly.next_after(monthly.first + timedelta(days=1)) > monthly.first + timedelta(days=28)
