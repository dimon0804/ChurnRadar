"""Действия для маркетолога: у каждой причины своё, у клиента одно главное."""

from __future__ import annotations

import pytest

from riskcore import merge_settings, noisy_or, score_customers

from .factories import AS_OF, make_account, make_service, make_tx
from .test_scoring import RISKY_CUSTOMER, score_one


def test_every_reason_has_action_and_money():
    result = score_one(*RISKY_CUSTOMER)
    assert result.reasons
    for reason in result.reasons:
        assert reason["action"]
        assert isinstance(reason["expected_loss"], int) and reason["expected_loss"] >= 0
    for service in result.detail["services"]:
        for reason in service["reasons"]:
            assert reason["action"]


def test_reason_losses_add_up_to_expected_loss():
    result = score_one(*RISKY_CUSTOMER)
    total = sum(t.loss for t in result.feature_totals)
    assert total == pytest.approx(sum(o.expected_loss for o in result.services))


def test_next_action_is_the_biggest_saving():
    result = score_one(*RISKY_CUSTOMER)
    recommendations = result.detail["recommendations"]
    assert 1 <= len(recommendations) <= 3
    savings = [r["expected_saving"] for r in recommendations]
    assert savings == sorted(savings, reverse=True)
    assert recommendations[0]["primary"] is True
    assert all("primary" not in r for r in recommendations[1:])
    assert result.detail["next_action"] == recommendations[0]
    best = max((t for t in result.feature_totals if t.points > 0.5), key=lambda t: t.saving)
    assert result.detail["next_action"]["code"] == best.code


def test_saving_is_counterfactual_loss_reduction():
    # Одна услуга, две причины: выключен сервер (0.35) и понедельная оплата (0.15 · 0.7).
    account = make_account()
    service = make_service(status="suspended", payment_term="week", price=700, expires_in_days=60)
    [result] = score_customers([account], [service], [], merge_settings(None), AS_OF)
    [outcome] = result.services
    totals = {t.code: t for t in result.feature_totals}
    revenue = outcome.revenue
    p = noisy_or([0.35, 0.105])
    assert totals["suspended"].saving == pytest.approx(revenue * (p - 0.105))
    assert totals["short_commitment"].saving == pytest.approx(revenue * (p - 0.35))
    assert result.detail["next_action"]["code"] == "suspended"
    assert result.detail["next_action"]["service_id"] == service.id


def test_account_level_action_has_no_service():
    account = make_account(balance=1_000, withdraw_balance=1_000)
    services = [make_service(id=1, payment_term="year", expires_in_days=200)]
    [result] = score_customers([account], services, [make_tx(1)], merge_settings(None), AS_OF)
    [action] = result.detail["recommendations"]
    assert action["code"] == "withdraw_pressure"
    assert "service_id" not in action
    assert "€10" in action["title"]


def test_unknown_customer_gets_win_back_as_next_action():
    account = make_account()
    [result] = score_customers([account], [make_service(status="deleted")], [], merge_settings(None), AS_OF)
    assert result.detail["next_action"]["code"] == "win_back"
    assert result.detail["next_action"]["primary"] is True


def test_quiet_customer_has_no_next_action():
    account = make_account(balance=500_000)
    services = [make_service(id=1, payment_term="year", price=10_000, expires_in_days=200)]
    [result] = score_customers([account], services, [make_tx(1)], merge_settings(None), AS_OF)
    assert result.detail["recommendations"] == []
    assert result.detail["next_action"] is None


def test_prolong_wait_action_names_deadline_and_promo():
    account = make_account(balance=1_000)
    service = make_service(status="prolong_wait", auto_prolong=False, price=5_000, expires_in_days=-2)
    [result] = score_customers([account], [service], [], merge_settings(None), AS_OF)
    action = result.detail["next_action"]
    assert action["code"] == "prolong_wait"
    assert action["title"].startswith("Срочный звонок")
    assert "через 3 дн." in action["title"]
    assert "промокод" in action["detail"] and "€40" in action["detail"]
