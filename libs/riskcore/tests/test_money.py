from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from riskcore import default_settings, monthly_price, score_customers
from riskcore.money import add_months, format_eur, horizon_revenue, renewal_plan, shortfall_moment

from .factories import AS_OF, make_account, make_service


@pytest.mark.parametrize(
    ("term", "price", "expected"),
    [
        ("hour", 10, 10 * 24 * 30),
        ("week", 700, 3_000),
        ("month", 999, 999),
        ("quarter_year", 3_000, 1_000),
        ("half_year", 6_000, 1_000),
        ("year", 12_000, 1_000),
    ],
)
def test_monthly_price_normalisation(term, price, expected):
    assert monthly_price(price, term) == pytest.approx(expected)


def test_horizon_revenue_scales_with_days():
    assert horizon_revenue(3_000, 30) == pytest.approx(3_000)
    assert horizon_revenue(3_000, 90) == pytest.approx(9_000)
    assert horizon_revenue(3_000, 7) == pytest.approx(700)


def test_add_months_clips_day():
    assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2026, 11, 30, tzinfo=UTC), 3) == datetime(2027, 2, 28, tzinfo=UTC)


@pytest.mark.parametrize(
    ("term", "expires_in", "horizon", "count"),
    [
        ("hour", 0.5 / 24, 30, 720),
        ("week", 1, 30, 5),
        ("week", 1, 7, 1),
        ("month", 5, 30, 1),
        ("month", 5, 90, 3),
        ("year", 5, 30, 1),
        ("month", 45, 30, 0),
    ],
)
def test_renewal_count_in_horizon(term, expires_in, horizon, count):
    plan = renewal_plan(make_service(payment_term=term, expires_in_days=expires_in), AS_OF, horizon)
    assert (plan.count if plan else 0) == count


def test_overdue_renewal_is_due_now():
    plan = renewal_plan(make_service(status="prolong_wait", expires_in_days=-10), AS_OF, 30)
    assert plan is not None
    assert plan.first == AS_OF


def test_no_plan_without_autoprolong_or_for_dead_service():
    assert renewal_plan(make_service(auto_prolong=False, expires_in_days=3), AS_OF, 30) is None
    assert renewal_plan(make_service(status="deleted", expires_in_days=3), AS_OF, 30) is None


def test_shortfall_moment_is_first_unpaid_renewal():
    first = renewal_plan(make_service(id=1, price=600, expires_in_days=2), AS_OF, 30)
    second = renewal_plan(make_service(id=2, price=600, expires_in_days=10), AS_OF, 30)
    assert first and second
    moment = shortfall_moment([first, second], spendable=1_000)
    assert moment is not None
    assert abs(moment - (AS_OF + timedelta(days=10))) <= timedelta(minutes=1)
    assert shortfall_moment([first, second], spendable=1_200) is None


def test_format_eur():
    assert format_eur(12_345) == "€123,45"
    assert format_eur(1_234_500) == "€12 345"


def test_customer_money_block():
    account = make_account(balance=6_000)
    services = [
        make_service(id=1, payment_term="week", price=700, status="suspended", expires_in_days=100),
        make_service(id=2, payment_term="year", price=12_000, expires_in_days=200),
        make_service(id=3, status="deleted", price=99_999),
    ]
    [result] = score_customers([account], services, [], default_settings(), AS_OF)
    money = result.detail["money"]
    assert money["monthly_revenue"] == 4_000 == result.monthly_revenue
    outcomes = [o for o in result.services if o.probability is not None]
    assert money["expected_loss"] == round(sum(o.probability * o.revenue for o in outcomes))
    assert money["exposure"] == sum(round(o.revenue) for o in outcomes if o.level in ("medium", "high"))
    assert money["balance_coverage_days"] == pytest.approx(6_000 / (4_000 / 30), abs=0.05)
    assert "€40" in money["explanation"]
    service = next(s for s in result.detail["services"] if s["id"] == 1)
    assert service["monthly_price"] == 3_000
