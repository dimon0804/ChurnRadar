from __future__ import annotations

import math
from dataclasses import replace

import pytest

from riskcore import Settings, decompose, feature_catalog, merge_settings, noisy_or, score_customers
from riskcore.scoring import LOG_CAP

from .factories import AS_OF, make_account, make_service, make_tx

TOLERANCE = 1e-6


def score_one(account, services, transactions=(), settings: Settings | None = None):
    settings = settings or merge_settings(None)
    [result] = score_customers([account], services, transactions, settings, AS_OF)
    return result


def with_weight(code: str, weight: float, enabled: bool = True) -> Settings:
    return merge_settings({"weights": {code: {"weight": weight, "enabled": enabled}}})


RISKY_CUSTOMER = (
    make_account(balance=2_000, withdraw_balance=1_500, bonus_balance=5_000, bonus_state="unlocked"),
    [
        make_service(id=1, payment_term="hour", price=20, expires_in_days=0.2),
        make_service(id=2, status="prolong_wait", price=3_000, expires_in_days=-2),
        make_service(id=3, auto_prolong=False, payment_term="week", price=900, expires_in_days=4),
        make_service(id=4, status="suspended", payment_term="year", price=12_000, expires_in_days=200),
        make_service(id=5, status="deleted"),
    ],
    [
        make_tx(1, service_id=1, status="not_performed"),
        make_tx(2, service_id=1, status="performed", days_ago=70),
        make_tx(3, service_id=4, status="not_performed"),
    ],
)


class TestNoisyOr:
    def test_formula(self):
        assert noisy_or([0.5, 0.5]) == pytest.approx(0.75)
        assert noisy_or([0.2, 0.3, 0.1]) == pytest.approx(1 - 0.8 * 0.7 * 0.9)

    def test_bounds(self):
        assert noisy_or([]) == 0.0
        assert noisy_or([1.0, 0.3]) == 1.0
        assert 0.0 <= noisy_or([0.99] * 20) <= 1.0

    @pytest.mark.parametrize(
        "strengths",
        [[0.5], [0.3, 0.6], [0.85, 0.15, 0.1, 0.02], [1.0, 0.4], [1.0, 1.0], [0.0, 0.0], [1e-9, 0.7]],
    )
    def test_decomposition_sums_exactly_to_probability(self, strengths):
        parts = decompose(strengths)
        assert sum(parts) == pytest.approx(noisy_or(strengths), abs=TOLERANCE)
        assert all(part >= 0 for part in parts)

    def test_decomposition_proportional_to_log_odds(self):
        parts = decompose([0.5, 0.75])
        assert parts[1] / parts[0] == pytest.approx(math.log(4) / math.log(2))

    def test_certain_signal_is_capped(self):
        parts = decompose([1.0, 0.5])
        assert parts[0] / parts[1] == pytest.approx(LOG_CAP / math.log(2))


class TestCustomerScore:
    def test_contributions_sum_to_score(self):
        result = score_one(*RISKY_CUSTOMER)
        assert result.risk_score is not None
        features_sum = sum(f["contribution"] for f in result.detail["features"])
        assert features_sum == pytest.approx(result.risk_score, abs=TOLERANCE)
        for service in result.detail["services"]:
            if service["risk_score"] is None:
                continue
            outcome = next(o for o in result.services if o.service.id == service["id"])
            assert sum(100 * h.contribution for h in outcome.hits) == pytest.approx(
                service["risk_score"], abs=TOLERANCE
            )

    def test_score_is_revenue_weighted_probability(self):
        result = score_one(*RISKY_CUSTOMER)
        live = [o for o in result.services if o.probability is not None]
        expected = 100 * sum(o.probability * o.revenue for o in live) / sum(o.revenue for o in live)
        assert result.risk_score == pytest.approx(expected)
        assert result.loyalty == pytest.approx(100 - expected)

    def test_reasons_are_top_features(self):
        result = score_one(*RISKY_CUSTOMER)
        assert 1 <= len(result.reasons) <= 5
        contributions = [r["contribution"] for r in result.reasons]
        assert contributions == sorted(contributions, reverse=True)
        assert all(c > 0.5 for c in contributions)
        by_code = {r["code"]: r for r in result.reasons}
        if "prolong_wait" in by_code:
            assert by_code["prolong_wait"]["service_id"] == 2
        for reason in result.reasons:
            if reason["code"] in {"churn_history", "inactivity", "bonus_dependency", "withdraw_pressure"}:
                assert reason["service_id"] is None

    def test_services_at_risk_and_level(self):
        result = score_one(*RISKY_CUSTOMER)
        live = [s for s in result.detail["services"] if s["risk_score"] is not None]
        assert result.services_total == 4
        assert result.services_at_risk == sum(1 for s in live if s["risk_score"] >= 35)
        assert result.risk_level == merge_settings(None).level_for(result.risk_score)

    def test_quiet_customer_is_low(self):
        account = make_account(balance=500_000)
        services = [make_service(id=1, payment_term="year", price=10_000, expires_in_days=200)]
        result = score_one(account, services, [make_tx(1)])
        assert result.risk_level == "low"
        assert result.risk_score == pytest.approx(0.0)
        assert result.reasons == []
        assert result.detail["recommendations"] == []

    def test_disabled_feature_is_ignored(self):
        account, services, txs = RISKY_CUSTOMER
        result = score_one(account, services, txs, with_weight("prolong_wait", 85, enabled=False))
        assert "prolong_wait" not in {f["code"] for f in result.detail["features"]}

    def test_thresholds_define_level(self):
        account = make_account()
        services = [make_service(status="suspended", payment_term="year", expires_in_days=200)]
        assert score_one(account, services).risk_score == pytest.approx(35.0)
        for medium, high, level in [(30.0, 80.0, "medium"), (40.0, 80.0, "low"), (20.0, 34.0, "high")]:
            settings = replace(merge_settings(None), thresholds={"medium": medium, "high": high})
            assert score_one(account, services, settings=settings).risk_level == level


class TestUnknown:
    def test_no_services(self):
        result = score_one(make_account(), [])
        assert result.risk_score is None and result.loyalty is None
        assert result.risk_level == "unknown"
        assert result.reasons[0]["code"] == "no_live_services"
        assert result.reasons[0]["contribution"] == 0
        assert "Нет действующих услуг" in result.reasons[0]["title"]

    def test_all_deleted(self):
        services = [make_service(id=1, status="deleted"), make_service(id=2, status="blocked")]
        result = score_one(make_account(), services)
        assert result.risk_level == "unknown"
        assert result.reasons[0]["title"].startswith("Все услуги клиента удалены")
        assert result.detail["recommendations"][0]["code"] == "win_back"
        assert all(s["risk_level"] == "unknown" for s in result.detail["services"])
        assert result.expected_loss == 0 and result.monthly_revenue == 0

    def test_only_free_services(self):
        result = score_one(make_account(), [make_service(price=0, status="suspended")])
        assert result.risk_level == "unknown"
        assert result.reasons[0]["code"] == "no_paid_services"
        assert result.detail["money"]["balance_coverage_days"] is None


class TestMonotonicity:
    @pytest.mark.parametrize("code", [spec.code for spec in feature_catalog()])
    def test_raising_weight_never_lowers_score(self, code):
        account, services, txs = RISKY_CUSTOMER
        previous = -1.0
        for weight in (0, 10, 25, 50, 75, 100):
            score = score_one(account, services, txs, with_weight(code, weight)).risk_score
            assert score is not None
            assert score >= previous - TOLERANCE
            previous = score

    def test_adding_signal_never_lowers_service_risk(self):
        account = make_account()
        calm = [make_service(payment_term="year", expires_in_days=200)]
        worse = [make_service(payment_term="year", expires_in_days=200, status="suspended")]
        assert score_one(account, worse).risk_score > score_one(account, calm).risk_score


def test_results_ordered_by_customer_and_isolated():
    accounts = [make_account(id=2), make_account(id=1)]
    services = [make_service(id=10, owner_id=1, status="suspended"), make_service(id=20, owner_id=2)]
    txs = [make_tx(1, service_id=10, status="not_performed"), make_tx(2, service_id=99)]
    results = score_customers(accounts, services, txs, merge_settings(None), AS_OF)
    assert [r.customer_id for r in results] == [1, 2]
    assert [t["id"] for t in results[0].detail["transactions"]] == [1]
    assert results[1].detail["transactions"] == []


def test_transactions_limited_and_newest_first():
    txs = [make_tx(i, days_ago=i) for i in range(1, 80)]
    result = score_one(make_account(), [make_service()], txs)
    shown = result.detail["transactions"]
    assert len(shown) == 50
    assert [t["id"] for t in shown] == list(range(1, 51))


def test_to_row_matches_table_columns():
    result = score_one(*RISKY_CUSTOMER)
    row = result.to_row()
    assert set(row) == {
        "customer_id",
        "email",
        "balance",
        "bonus_balance",
        "bonus_state",
        "withdraw_balance",
        "total_replenished",
        "risk_score",
        "risk_level",
        "loyalty",
        "reasons",
        "services_total",
        "services_at_risk",
        "monthly_revenue",
        "expected_loss",
        "exposure",
        "detail",
        "model_version",
        "scored_at",
    }
    assert set(row["detail"]) == {"services", "features", "transactions", "money", "recommendations"}
    assert row["scored_at"] == AS_OF
