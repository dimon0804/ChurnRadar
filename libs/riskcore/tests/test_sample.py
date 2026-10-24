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


def test_case_numbers_match_readme(sample_results):
    """Цифры из README и docs/model.md: 4 высокого, 2 среднего, 4 без оценки, €2 297,83."""
    results, services, settings = sample_results
    levels = Counter(r.risk_level for r in results)
    assert dict(levels) == {"high": 4, "medium": 2, "unknown": 4}
    summary = build_summary(results, services, [], settings, AS_OF)
    assert summary["expected_loss"] == 229_783
    stats = summary["risk_scores"]
    scores = [r.risk_score for r in results if r.risk_score is not None]
    assert stats["scored"] == 6
    assert stats["avg"] == pytest.approx(sum(scores) / 6)


def test_customer_5_money_is_consistent(sample_results):
    """Дата в причине «не хватит денег» и «баланса хватит» в блоке денег — один расчёт."""
    results, _, _ = sample_results
    result = next(r for r in results if r.customer_id == 5)
    money = result.detail["money"]
    [reason] = [r for r in result.reasons if r["code"] == "insufficient_funds"]
    assert money["funds_run_out_at"] == "2026-10-21T10:00:00Z"
    assert money["balance_coverage_days"] == pytest.approx(1.8)
    assert "хватит до 21.10.2026" in reason["title"]
    assert "до 21.10.2026" in money["explanation"]
    # почасовой сервер: по прайсу тысячи евро, потеря считается от того, что клиент может заплатить
    assert "По прайсу продления service-0025" in reason["title"]
    assert "потерю считаем от €506,78" in reason["title"]
    assert "€4 696,68" not in reason["title"]


def test_reasons_have_short_labels(sample_results):
    results, _, _ = sample_results
    for result in results:
        titles = {f["code"]: f["title"] for f in result.detail["features"]}
        for reason in result.reasons:
            assert reason["label"] and len(reason["label"]) < len(reason["title"])
            if reason["code"] in titles:
                assert reason["label"] == titles[reason["code"]]
        for service in result.detail["services"]:
            for reason in service["reasons"]:
                assert reason["label"] == titles[reason["code"]]
