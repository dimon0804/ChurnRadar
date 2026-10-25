from __future__ import annotations

from datetime import date, timedelta

import pytest

from riskcore import StatusChange, build_summary, decision_moment, default_settings, score_customers
from riskcore.types import PROLONG_WAIT_GRACE

from .factories import AS_OF, make_account, make_service, make_tx

HORIZON = 30


@pytest.fixture
def portfolio():
    accounts = [make_account(id=1, balance=500), make_account(id=2, balance=900_000), make_account(id=3)]
    services = [
        make_service(id=1, owner_id=1, status="prolong_wait", expires_in_days=-2, price=2_000),
        make_service(id=2, owner_id=1, payment_term="hour", price=15, expires_in_days=0.3),
        make_service(id=3, owner_id=1, auto_prolong=False, expires_in_days=12, price=4_000),
        make_service(
            id=4, owner_id=2, status="suspended", payment_term="year", expires_in_days=120, price=24_000
        ),
        make_service(
            id=5, owner_id=2, auto_prolong=False, payment_term="week", expires_in_days=-1, price=700
        ),
        make_service(id=6, owner_id=2, status="prolong_wait", expires_in_days=-40, price=300),
        make_service(id=7, owner_id=3, status="deleted", expires_in_days=-50),
    ]
    transactions = [make_tx(1, service_id=3, status="not_performed"), make_tx(2, service_id=4, days_ago=80)]
    history = [
        StatusChange(7, 3, "prolong_wait", "deleted", AS_OF - timedelta(days=3), estimated=True),
        StatusChange(8, 3, "prolong_wait", "deleted", AS_OF - timedelta(days=3, hours=2)),
        StatusChange(9, 3, "active", "suspended", AS_OF - timedelta(days=5)),
        StatusChange(10, 3, "prolong_wait", "deleted", AS_OF - timedelta(days=45)),
    ]
    settings = default_settings()
    results = score_customers(accounts, services, transactions, settings, AS_OF)
    summary = build_summary(results, services, history, settings, AS_OF)
    return results, services, summary


def test_fact_and_forecast_do_not_overlap(portfolio):
    _, _, summary = portfolio
    timeline = summary["timeline"]
    fact = [p for p in timeline if p["kind"] == "fact"]
    forecast = [p for p in timeline if p["kind"] == "forecast"]
    assert len(fact) == 30 and len(forecast) == HORIZON
    fact_dates = {p["date"] for p in fact}
    forecast_dates = {p["date"] for p in forecast}
    assert not fact_dates & forecast_dates
    assert max(fact_dates) == AS_OF.date().isoformat()
    assert min(forecast_dates) == (AS_OF.date() + timedelta(days=1)).isoformat()
    dates = [p["date"] for p in timeline]
    assert dates == sorted(dates) and len(dates) == len(set(dates))


def test_fact_counts_deletions_by_day(portfolio):
    _, _, summary = portfolio
    fact = {p["date"]: p for p in summary["timeline"] if p["kind"] == "fact"}
    three_days_ago = (AS_OF.date() - timedelta(days=3)).isoformat()
    assert fact[three_days_ago]["deletions"] == 2
    assert fact[three_days_ago]["estimated_deletions"] == 1
    assert sum(p["deletions"] for p in fact.values()) == 2
    assert sum(p["estimated_deletions"] for p in fact.values()) == 1
    assert all(p["expected_nonrenewals"] is None for p in fact.values())


def test_at_risk_is_known_in_fact_only_for_snapshot_day(portfolio):
    _, _, summary = portfolio
    fact = [p for p in summary["timeline"] if p["kind"] == "fact"]
    assert all(p["at_risk_services"] is None for p in fact[:-1])
    assert fact[-1]["date"] == AS_OF.date().isoformat()
    assert fact[-1]["at_risk_services"] == summary["services_at_risk"]


def test_deleted_services_take_date_from_history(portfolio):
    _, _, summary = portfolio
    # услуга 7 удалена, дата из истории (оценочная запись ingest), а не из expires_at
    assert summary["deleted_services"] == {
        "total": 1,
        "last_date": (AS_OF - timedelta(days=3)).date().isoformat(),
        "last_estimated": True,
    }


def test_deleted_services_before_fact_window_are_still_counted():
    # как в данных кейса: удалённые услуги есть, но все удалены раньше окна факта
    accounts = [make_account(id=1)]
    services = [
        make_service(id=1, owner_id=1, status="deleted", expires_in_days=-36),
        make_service(id=2, owner_id=1, status="deleted", expires_in_days=-200),
        make_service(id=3, owner_id=1),
    ]
    settings = default_settings()
    results = score_customers(accounts, services, [], settings, AS_OF)
    summary = build_summary(results, services, [], settings, AS_OF)
    assert sum(p["deletions"] for p in summary["timeline"] if p["kind"] == "fact") == 0
    assert summary["deleted_services"] == {
        "total": 2,
        "last_date": (AS_OF - timedelta(days=36) + PROLONG_WAIT_GRACE).date().isoformat(),
        "last_estimated": True,
    }


def test_deleted_services_empty():
    accounts = [make_account(id=1)]
    services = [make_service(id=1, owner_id=1)]
    settings = default_settings()
    results = score_customers(accounts, services, [], settings, AS_OF)
    summary = build_summary(results, services, iter([]), settings, AS_OF)
    assert summary["deleted_services"] == {"total": 0, "last_date": None, "last_estimated": False}


def test_forecast_sums_to_probabilities_inside_horizon(portfolio):
    results, _, summary = portfolio
    horizon_end = AS_OF + timedelta(days=HORIZON)
    expected = 0.0
    for outcome in (o for r in results for o in r.services if o.probability is not None):
        moment = decision_moment(outcome.service)
        if moment is None or moment <= horizon_end:
            expected += outcome.loss_probability
    forecast = [p for p in summary["timeline"] if p["kind"] == "forecast"]
    assert sum(p["expected_nonrenewals"] for p in forecast) == pytest.approx(expected, abs=1e-6)


def test_forecast_places_decisions_on_right_days(portfolio):
    results, _, summary = portfolio
    forecast = {date.fromisoformat(p["date"]): p for p in summary["timeline"] if p["kind"] == "forecast"}
    outcomes = {o.service.id: o for r in results for o in r.services}
    hourly_share = outcomes[2].loss_probability / HORIZON
    day_12 = (AS_OF + timedelta(days=12)).date()
    assert forecast[day_12]["expected_nonrenewals"] == pytest.approx(
        outcomes[3].loss_probability + hourly_share
    )
    first_day = AS_OF.date() + timedelta(days=1)
    overdue = outcomes[5].loss_probability + outcomes[6].loss_probability
    pending_deletion = (AS_OF - timedelta(days=2) + PROLONG_WAIT_GRACE).date()
    assert pending_deletion == AS_OF.date() + timedelta(days=3)
    assert forecast[first_day]["expected_nonrenewals"] == pytest.approx(overdue + hourly_share)
    assert forecast[pending_deletion]["expected_nonrenewals"] == pytest.approx(
        outcomes[1].loss_probability + hourly_share
    )


def test_at_risk_services_never_grow(portfolio):
    _, _, summary = portfolio
    counts = [p["at_risk_services"] for p in summary["timeline"] if p["kind"] == "forecast"]
    assert counts[0] == summary["services_at_risk"]
    assert counts == sorted(counts, reverse=True)


def test_totals(portfolio):
    results, _, summary = portfolio
    assert summary["customers_total"] == 3
    assert summary["by_level"]["unknown"] == 1
    assert sum(summary["by_level"].values()) == 3
    assert summary["services_total"] == 6
    assert summary["expected_loss"] == sum(r.expected_loss for r in results)
    assert summary["exposure"] == sum(r.exposure for r in results)
    assert summary["monthly_revenue_total"] == sum(r.monthly_revenue for r in results)
    assert 0 <= summary["revenue_at_risk_share"] <= 1
    assert summary["horizon_days"] == HORIZON
    assert summary["money_explanation"]


def test_top_reasons(portfolio):
    results, _, summary = portfolio
    rows = {r["code"]: r for r in summary["top_reasons"]}
    scored = [r for r in results if r.risk_score is not None]
    for code, row in rows.items():
        having = [r for r in scored if code in {t.code for t in r.feature_totals}]
        assert row["customers"] == len(having) > 0
        primary = sum(1 for r in scored if r.reasons and r.reasons[0]["code"] == code)
        assert row["primary_customers"] == primary <= row["customers"]
        # деньги считаются по тем же клиентам, что и количество
        loss = sum(t.loss for r in having for t in r.feature_totals if t.code == code)
        assert row["expected_loss"] == round(loss)
        assert row["title"]
    assert sum(r["primary_customers"] for r in rows.values()) == sum(1 for r in scored if r.reasons)
    total_loss = sum(o.expected_loss for r in results for o in r.services if o.probability is not None)
    assert sum(r["expected_loss"] for r in summary["top_reasons"]) == pytest.approx(total_loss, abs=len(rows))


def test_top_reasons_never_show_zero_customers(portfolio):
    _, _, summary = portfolio
    assert summary["top_reasons"]
    assert all(row["customers"] > 0 for row in summary["top_reasons"])
    losses = [row["expected_loss"] for row in summary["top_reasons"]]
    assert losses == sorted(losses, reverse=True)


def test_risk_scores_distribution(portfolio):
    results, _, summary = portfolio
    stats = summary["risk_scores"]
    scores = [r.risk_score for r in results if r.risk_score is not None]
    assert stats["scored"] == len(scores) == 2
    assert stats["avg"] == pytest.approx(sum(scores) / len(scores))
    assert stats["max"] == pytest.approx(max(scores))
    assert [b["le"] for b in stats["buckets"]] == list(range(10, 101, 10))
    for bucket in stats["buckets"]:
        assert bucket["count"] == sum(1 for s in scores if s <= bucket["le"])
    assert stats["buckets"][-1]["count"] == stats["scored"]


def test_risk_scores_without_scored_customers():
    settings = default_settings()
    results = score_customers([make_account(id=1)], [], [], settings, AS_OF)
    stats = build_summary(results, [], [], settings, AS_OF)["risk_scores"]
    assert stats["scored"] == 0 and stats["avg"] is None and stats["max"] is None
    assert all(b["count"] == 0 for b in stats["buckets"])
