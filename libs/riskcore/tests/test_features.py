from __future__ import annotations

import pytest

from riskcore import feature_catalog
from riskcore.registry import get_feature

from .factories import account_ctx, make_account, make_service, make_tx, service_ctx

EXPECTED_DEFAULTS = {
    "prolong_wait": 85,
    "autoprolong_off_expiring": 60,
    "insufficient_funds": 55,
    "suspended": 35,
    "failed_payments": 30,
    "short_commitment": 15,
    "churn_history": 40,
    "inactivity": 20,
    "bonus_dependency": 15,
    "withdraw_pressure": 10,
}


def feature(code: str):
    found = get_feature(code)
    assert found is not None
    return found


def test_catalog_has_all_v1_features_with_defaults():
    catalog = {spec.code: spec for spec in feature_catalog()}
    assert {code: catalog[code].default_weight for code in EXPECTED_DEFAULTS} == EXPECTED_DEFAULTS
    for spec in catalog.values():
        assert spec.min == 0 and spec.max == 100
        assert spec.title and spec.description and spec.group
    assert {
        catalog[c].level for c in ("churn_history", "inactivity", "bonus_dependency", "withdraw_pressure")
    } == {"account"}


class TestProlongWait:
    def test_not_enough_money_gives_full_strength(self):
        service = make_service(status="prolong_wait", price=5_000, expires_in_days=-2)
        signal = feature("prolong_wait").compute(service_ctx(service, make_account(balance=1_000)))
        assert signal is not None
        assert signal.value == 1.0
        assert "service-0001" in signal.explanation
        assert "~3 дн." in signal.explanation
        assert "€40" in signal.explanation

    def test_enough_money_gives_partial_strength(self):
        service = make_service(status="prolong_wait", price=5_000, expires_in_days=-1)
        signal = feature("prolong_wait").compute(service_ctx(service, make_account(balance=5_000)))
        assert signal is not None and signal.value == 0.6

    def test_unlocked_bonus_counts_as_money(self):
        service = make_service(status="prolong_wait", price=5_000, expires_in_days=-1)
        account = make_account(balance=3_000, bonus_balance=2_000, bonus_state="unlocked")
        signal = feature("prolong_wait").compute(service_ctx(service, account))
        assert signal is not None and signal.value == 0.6

    def test_overdue_deletion_is_explained(self):
        service = make_service(status="prolong_wait", expires_in_days=-30)
        signal = feature("prolong_wait").compute(service_ctx(service))
        assert signal is not None
        assert "в любой момент" in signal.explanation
        recommendation = feature("prolong_wait").recommend(signal)
        assert recommendation is not None and "в любой момент" in recommendation.title

    @pytest.mark.parametrize("status", ["active", "suspended"])
    def test_other_statuses_do_not_fire(self, status):
        assert feature("prolong_wait").compute(service_ctx(make_service(status=status))) is None


class TestAutoprolongOffExpiring:
    def test_value_falls_linearly_with_days_to_expiry(self):
        f = feature("autoprolong_off_expiring")
        near = f.compute(service_ctx(make_service(auto_prolong=False, expires_in_days=3)))
        mid = f.compute(service_ctx(make_service(auto_prolong=False, expires_in_days=15)))
        assert near is not None and mid is not None
        assert near.value == pytest.approx(0.9)
        assert mid.value == pytest.approx(0.5)

    def test_expired_gives_one(self):
        signal = feature("autoprolong_off_expiring").compute(
            service_ctx(make_service(auto_prolong=False, expires_in_days=-4))
        )
        assert signal is not None and signal.value == 1.0

    def test_outside_horizon_does_not_fire(self):
        f = feature("autoprolong_off_expiring")
        assert f.compute(service_ctx(make_service(auto_prolong=False, expires_in_days=31))) is None
        assert f.compute(service_ctx(make_service(auto_prolong=False, expires_in_days=30))) is None

    def test_horizon_setting_matters(self):
        ctx = service_ctx(make_service(auto_prolong=False, expires_in_days=45), horizon_days=90)
        signal = feature("autoprolong_off_expiring").compute(ctx)
        assert signal is not None and signal.value == pytest.approx(0.5)

    def test_autoprolong_on_or_prolong_wait_does_not_fire(self):
        f = feature("autoprolong_off_expiring")
        assert f.compute(service_ctx(make_service(auto_prolong=True, expires_in_days=2))) is None
        waiting = make_service(auto_prolong=False, status="prolong_wait", expires_in_days=-1)
        assert f.compute(service_ctx(waiting)) is None


class TestInsufficientFunds:
    def test_share_of_missing_money(self):
        service = make_service(price=10_000, expires_in_days=5)
        signal = feature("insufficient_funds").compute(service_ctx(service, make_account(balance=4_000)))
        assert signal is not None
        assert signal.value == pytest.approx(0.6)
        assert "€60" in signal.explanation

    def test_counts_all_renewals_of_customer(self):
        service = make_service(id=1, price=6_000, expires_in_days=5)
        other = make_service(id=2, price=6_000, expires_in_days=10)
        signal = feature("insufficient_funds").compute(
            service_ctx(service, make_account(balance=9_000), others=[other])
        )
        assert signal is not None and signal.value == pytest.approx(0.25)

    def test_hourly_service_costs_every_hour_in_horizon(self):
        service = make_service(payment_term="hour", price=10, expires_in_days=0.5 / 24)
        ctx = service_ctx(service, make_account(balance=0))
        assert ctx.renewal is not None
        assert ctx.renewal.count == 30 * 24
        assert ctx.funds.upcoming_renewals_cost == 30 * 24 * 10

    def test_enough_money_does_not_fire(self):
        service = make_service(price=10_000, expires_in_days=5)
        assert (
            feature("insufficient_funds").compute(service_ctx(service, make_account(balance=10_000))) is None
        )

    def test_renewal_outside_horizon_does_not_fire(self):
        service = make_service(price=10_000, expires_in_days=40)
        assert feature("insufficient_funds").compute(service_ctx(service, make_account(balance=0))) is None

    def test_autoprolong_off_does_not_fire(self):
        service = make_service(auto_prolong=False, price=10_000, expires_in_days=5)
        assert feature("insufficient_funds").compute(service_ctx(service, make_account(balance=0))) is None


class TestSuspended:
    def test_fires_only_for_suspended(self):
        f = feature("suspended")
        signal = f.compute(service_ctx(make_service(status="suspended")))
        assert signal is not None and signal.value == 1.0
        assert f.compute(service_ctx(make_service(status="active"))) is None


class TestFailedPayments:
    def test_share_of_failed_prolongs(self):
        txs = [
            make_tx(1, status="not_performed"),
            make_tx(2, status="performed"),
            make_tx(3, status="performed"),
            make_tx(4, status="not_performed", type="order"),
        ]
        signal = feature("failed_payments").compute(service_ctx(make_service(), transactions=txs))
        assert signal is not None
        assert signal.value == pytest.approx(1 / 3)
        assert "1 из 3" in signal.explanation

    def test_needs_at_least_one_prolong(self):
        txs = [make_tx(1, status="not_performed", type="order")]
        assert feature("failed_payments").compute(service_ctx(make_service(), transactions=txs)) is None

    def test_all_successful_does_not_fire(self):
        txs = [make_tx(1), make_tx(2)]
        assert feature("failed_payments").compute(service_ctx(make_service(), transactions=txs)) is None

    def test_ignores_other_services(self):
        txs = [make_tx(1, service_id=2, status="not_performed")]
        ctx = service_ctx(make_service(id=1), others=[make_service(id=2)], transactions=txs)
        assert feature("failed_payments").compute(ctx) is None


class TestShortCommitment:
    @pytest.mark.parametrize(
        ("term", "value"),
        [
            ("hour", 1.0),
            ("week", 0.7),
            ("month", 0.3),
            ("quarter_year", None),
            ("half_year", None),
            ("year", None),
        ],
    )
    def test_strength_by_term(self, term, value):
        signal = feature("short_commitment").compute(service_ctx(make_service(payment_term=term)))
        assert (signal.value if signal else None) == value


class TestChurnHistory:
    def test_share_of_gone_services(self):
        services = [
            make_service(id=1),
            make_service(id=2, status="deleted"),
            make_service(id=3, status="blocked"),
            make_service(id=4),
        ]
        signal = feature("churn_history").compute(account_ctx(services=services))
        assert signal is not None and signal.value == pytest.approx(0.5)

    def test_needs_two_services(self):
        assert (
            feature("churn_history").compute(account_ctx(services=[make_service(status="deleted")])) is None
        )

    def test_no_gone_services(self):
        services = [make_service(id=1), make_service(id=2)]
        assert feature("churn_history").compute(account_ctx(services=services)) is None


class TestInactivity:
    @pytest.mark.parametrize(
        ("days_ago", "value"),
        [(10, None), (60, None), (75, 0.5), (90, 1.0), (200, 1.0)],
    )
    def test_linear_between_60_and_90_days(self, days_ago, value):
        ctx = account_ctx(services=[make_service()], transactions=[make_tx(1, days_ago=days_ago)])
        signal = feature("inactivity").compute(ctx)
        if value is None:
            assert signal is None
        else:
            assert signal is not None and signal.value == pytest.approx(value, abs=1e-3)

    def test_uses_latest_performed_payment(self):
        txs = [
            make_tx(1, days_ago=200),
            make_tx(2, days_ago=5),
            make_tx(3, days_ago=1, status="not_performed"),
        ]
        assert feature("inactivity").compute(account_ctx(services=[make_service()], transactions=txs)) is None

    def test_no_transactions_is_unknown(self):
        assert feature("inactivity").compute(account_ctx(services=[make_service()])) is None

    def test_only_failed_transactions_means_inactive(self):
        txs = [make_tx(1, status="not_performed")]
        signal = feature("inactivity").compute(account_ctx(services=[make_service()], transactions=txs))
        assert signal is not None and signal.value == 1.0


class TestBonusDependency:
    def test_formula(self):
        account = make_account(balance=1_000, bonus_balance=3_000, bonus_state="unlocked")
        signal = feature("bonus_dependency").compute(account_ctx(account))
        assert signal is not None and signal.value == pytest.approx(0.5)

    def test_no_own_money(self):
        account = make_account(balance=0, bonus_balance=3_000, bonus_state="unlocked")
        signal = feature("bonus_dependency").compute(account_ctx(account))
        assert signal is not None and signal.value == 1.0

    @pytest.mark.parametrize(
        ("balance", "bonus", "state"),
        [(1_000, 3_000, "locked"), (3_000, 3_000, "unlocked"), (5_000, 1_000, "unlocked")],
    )
    def test_does_not_fire(self, balance, bonus, state):
        account = make_account(balance=balance, bonus_balance=bonus, bonus_state=state)
        assert feature("bonus_dependency").compute(account_ctx(account)) is None


class TestWithdrawPressure:
    def test_share_of_balance(self):
        signal = feature("withdraw_pressure").compute(
            account_ctx(make_account(balance=10_000, withdraw_balance=2_500))
        )
        assert signal is not None and signal.value == pytest.approx(0.25)
        assert "25%" in signal.explanation

    def test_clipped_to_one(self):
        signal = feature("withdraw_pressure").compute(
            account_ctx(make_account(balance=1_000, withdraw_balance=5_000))
        )
        assert signal is not None and signal.value == 1.0

    def test_zero_balance_is_unknown(self):
        assert (
            feature("withdraw_pressure").compute(account_ctx(make_account(balance=0, withdraw_balance=10)))
            is None
        )


def test_every_feature_has_recommendation_with_facts(settings):
    contexts = {
        "prolong_wait": service_ctx(
            make_service(status="prolong_wait", expires_in_days=-1), make_account(balance=0)
        ),
        "autoprolong_off_expiring": service_ctx(make_service(auto_prolong=False, expires_in_days=3)),
        "insufficient_funds": service_ctx(make_service(expires_in_days=3), make_account(balance=0)),
        "suspended": service_ctx(make_service(status="suspended")),
        "failed_payments": service_ctx(make_service(), transactions=[make_tx(1, status="not_performed")]),
        "short_commitment": service_ctx(make_service(payment_term="hour")),
        "churn_history": account_ctx(services=[make_service(id=1), make_service(id=2, status="deleted")]),
        "inactivity": account_ctx(services=[make_service()], transactions=[make_tx(1, days_ago=100)]),
        "bonus_dependency": account_ctx(make_account(balance=0, bonus_balance=10, bonus_state="unlocked")),
        "withdraw_pressure": account_ctx(make_account(balance=10, withdraw_balance=5)),
    }
    assert set(contexts) == set(EXPECTED_DEFAULTS)
    for code, ctx in contexts.items():
        f = feature(code)
        signal = f.compute(ctx)
        assert signal is not None, code
        assert 0 < signal.value <= 1
        recommendation = f.recommend(signal)
        assert recommendation is not None and recommendation.code == code
        assert recommendation.title
