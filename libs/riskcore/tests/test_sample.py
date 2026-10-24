"""Прогон модели на тестовом наборе кейса (10 клиентов, 30 услуг, 100 транзакций)."""

from __future__ import annotations

from collections import Counter

import pytest

from riskcore import build_summary, default_settings, parse_snapshot, score_customers

from .factories import AS_OF


@pytest.fixture(scope="module")
def sample_results(sample_data):
    accounts, services, transactions = parse_snapshot(sample_data)
    settings = default_settings()
    return score_customers(accounts, services, transactions, settings, AS_OF), services, settings


def test_all_customers_scored(sample_results):
    results, _, _ = sample_results
    assert [r.customer_id for r in results] == list(range(1, 11))
    for result in results:
        assert result.risk_level in {"high", "medium", "low", "unknown"}
        assert result.reasons, f"клиент {result.customer_id} без объяснения"
        if result.risk_score is None:
            assert result.risk_level == "unknown"
        else:
            assert 0 <= result.risk_score <= 100
            assert sum(f["contribution"] for f in result.detail["features"]) == pytest.approx(
                result.risk_score, abs=1e-6
            )


def test_level_distribution_is_explainable(sample_results):
    results, services, _ = sample_results
    live_owners = {s.owner_id for s in services if s.is_live}
    for result in results:
        assert (result.risk_level == "unknown") == (result.customer_id not in live_owners)
    levels = Counter(r.risk_level for r in results)
    assert levels["unknown"] == 10 - len(live_owners)
    assert levels["high"] + levels["medium"] >= 1


def test_hourly_revenue_is_capped_by_what_customer_can_pay(sample_results):
    results, _, _ = sample_results
    by_id = {r.customer_id: r for r in results}
    for customer_id, service_id in ((5, 25), (9, 19)):
        result = by_id[customer_id]
        [outcome] = [o for o in result.services if o.service.id == service_id]
        assert outcome.service.payment_term == "hour"
        assert outcome.revenue < outcome.contract_revenue
        assert outcome.revenue_note is not None
        # Выручка за горизонт не больше того, что клиент способен заплатить.
        assert result.horizon_revenue <= result.balance + result.total_replenished


def test_every_scored_customer_has_next_action(sample_results):
    results, _, _ = sample_results
    for result in results:
        action = result.detail["next_action"]
        assert action is not None, result.customer_id
        assert action["primary"] is True
        if result.risk_score is not None:
            assert action["expected_saving"] > 0
            assert all(reason["action"] for reason in result.reasons)


def test_sample_summary(sample_results):
    results, services, settings = sample_results
    summary = build_summary(results, services, [], settings, AS_OF)
    assert summary["customers_total"] == 10
    assert summary["services_total"] == sum(1 for s in services if s.is_live)


def test_print_sample_table(sample_results, capsys):
    results, _, _ = sample_results
    lines = [f"{'id':>3} | {'score':>6} | {'level':<7} | {'loss, EUR':>9} | top reason"]
    for r in results:
        score = "-" if r.risk_score is None else f"{r.risk_score:.1f}"
        top = r.reasons[0]["code"] if r.reasons else "-"
        lines.append(
            f"{r.customer_id:>3} | {score:>6} | {r.risk_level:<7} | {r.expected_loss / 100:>9.2f} | {top}"
        )
    with capsys.disabled():
        print("\n" + "\n".join(lines))
