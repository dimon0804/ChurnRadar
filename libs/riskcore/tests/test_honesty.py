"""Группы коррелированных признаков, калибровка score → вероятность, челленджер и монитор дрейфа."""

from __future__ import annotations

import json
from itertools import pairwise

import pytest

from riskcore import calibration, decompose, drift, merge_settings, noisy_or, parse_snapshot, score_customers
from riskcore.groups import GROUPS, combine, default_groups
from riskcore.types import GroupSetting

from .factories import AS_OF, make_account, make_service

PAYMENT = ("prolong_wait", "insufficient_funds", "failed_payments")


def _groups(enabled: bool, cap: float = 60.0) -> dict[str, GroupSetting]:
    return {"payment": GroupSetting(cap=cap, enabled=enabled)}


# --- группы -------------------------------------------------------------------------------------


def test_disabled_groups_equal_plain_noisy_or():
    strengths = [0.51, 0.44, 0.3, 0.105]
    codes = [*PAYMENT, "short_commitment"]
    p, parts, groups = combine(strengths, codes, _groups(False))
    assert p == pytest.approx(noisy_or(strengths))
    assert parts == pytest.approx(decompose(strengths))
    assert groups == [None] * 4


def test_group_cap_limits_one_fact_counted_three_times():
    """Пример жюри: одна неоплаченная услуга давала 80 из 82 баллов тремя причинами."""
    strengths = [0.51, 0.44, 0.3]
    p_plain = noisy_or(strengths)
    p, parts, groups = combine(strengths, list(PAYMENT), _groups(True, cap=60.0))
    assert p_plain == pytest.approx(0.808, abs=1e-3)
    assert p == pytest.approx(0.60)
    assert sum(parts) == pytest.approx(p)
    assert groups == ["payment"] * 3
    # внутри группы порядок вкладов прежний: сильнее сигнал — больше вклад
    assert parts[0] > parts[1] > parts[2]


def test_group_and_other_features_still_combine_by_noisy_or():
    strengths = [0.51, 0.44, 0.35]
    codes = ["prolong_wait", "insufficient_funds", "suspended"]
    p, parts, _ = combine(strengths, codes, _groups(True, cap=60.0))
    assert p == pytest.approx(1 - (1 - 0.6) * (1 - 0.35))
    assert sum(parts) == pytest.approx(p)


def test_cap_above_group_probability_changes_nothing():
    strengths = [0.3, 0.2]
    p, parts, _ = combine(strengths, ["prolong_wait", "failed_payments"], _groups(True, cap=100.0))
    assert p == pytest.approx(noisy_or(strengths))
    assert sum(parts) == pytest.approx(p)


def test_group_is_monotone_in_strength():
    previous = -1.0
    for s in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0):
        p, _, _ = combine([s, 0.2], ["prolong_wait", "suspended"], _groups(True, cap=70.0))
        assert p >= previous
        previous = p


def test_group_shows_in_reasons(settings):
    account = make_account(balance=0, total_replenished=10_000)
    service = make_service(status="prolong_wait", expires_in_days=-1, auto_prolong=True, price=5_000)
    stored = {"groups": {"payment": {"enabled": True, "cap": 60}}, "calibrated": False}
    result = score_customers([account], [service], [], merge_settings(stored), AS_OF)[0]
    payment = [r for r in result.reasons if r["code"] in PAYMENT]
    assert payment
    assert all(r["correlation_group"] == "payment" for r in payment)
    assert all(r["correlation_group_title"] == GROUPS["payment"].title for r in payment)
    # вклады по-прежнему сходятся в score
    features = result.detail["features"]
    assert sum(f["contribution"] for f in features) == pytest.approx(result.risk_score, abs=1e-6)


def test_groups_off_by_default_and_round_trip():
    settings = merge_settings(None)
    assert settings.groups == default_groups()
    assert not settings.groups["payment"]["enabled"]
    again = merge_settings(
        merge_settings({"groups": {"payment": {"enabled": True, "cap": 250}}}).to_storage()
    )
    assert again.groups["payment"] == {"enabled": True, "cap": 100.0}


# --- калибровка ---------------------------------------------------------------------------------


def test_shipped_table_is_monotone_and_bounded():
    table = calibration.tables()
    assert table is not None, "нет libs/riskcore/riskcore/model_tables.json: python -m tools.calibration"
    xs, ys = table["isotonic"]["x"], table["isotonic"]["y"]
    assert len(xs) == len(ys) >= 2
    assert all(a <= b for a, b in pairwise(xs))
    assert all(a <= b for a, b in pairwise(ys))
    assert all(0.0 <= y <= 1.0 for y in ys)


def test_calibrate_is_monotone_on_grid():
    values = [calibration.calibrate(i / 200, 30) for i in range(201)]
    assert all(v is not None and 0.0 <= v <= 1.0 for v in values)
    assert all(a <= b + 1e-12 for a, b in pairwise(values))


def test_calibrate_other_horizon_keeps_daily_hazard():
    q30 = calibration.calibrate(0.5, 30)
    q60 = calibration.calibrate(0.5, 60)
    assert q30 is not None and q60 is not None
    assert q60 == pytest.approx(1 - (1 - q30) ** 2)


def test_missing_tables_fall_back_to_raw(monkeypatch, tmp_path):
    monkeypatch.setattr(calibration, "TABLES_FILE", tmp_path / "nope.json")
    calibration.tables.cache_clear()
    try:
        assert calibration.calibrate(0.4, 30) is None
        assert calibration.challenger_probability({"prolong_wait": 1.0}) is None
        assert not calibration.available()
    finally:
        calibration.tables.cache_clear()


def test_interpolation_between_points(monkeypatch, tmp_path):
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps({"horizon_days": 30, "isotonic": {"x": [0.0, 0.5, 1.0], "y": [0.0, 0.1, 0.9]}})
    )
    monkeypatch.setattr(calibration, "TABLES_FILE", path)
    calibration.tables.cache_clear()
    try:
        assert calibration.calibrate(0.25, 30) == pytest.approx(0.05)
        assert calibration.calibrate(0.75, 30) == pytest.approx(0.5)
        assert calibration.calibrate(2.0, 30) == pytest.approx(0.9)
    finally:
        calibration.tables.cache_clear()


def test_money_uses_calibrated_probability_score_stays_ranking(sample_data):
    accounts, services, transactions = parse_snapshot(sample_data)
    raw = score_customers(accounts, services, transactions, merge_settings({"calibrated": False}), AS_OF)
    cal = score_customers(accounts, services, transactions, merge_settings({"calibrated": True}), AS_OF)
    for a, b in zip(raw, cal, strict=True):
        assert a.risk_score == b.risk_score
        assert a.risk_level == b.risk_level
        if b.risk_score is None:
            continue
        expected = sum(o.calibrated * o.revenue for o in b.services if o.probability is not None)
        assert b.expected_loss == round(expected)
        assert b.calibrated_probability == pytest.approx(expected / b.horizon_revenue)
        # деньги по причинам сходятся в деньги клиента (с точностью до округления каждой причины)
        reasons = [r for r in b.reasons if "expected_loss" in r]
        assert sum(r["expected_loss"] for r in reasons) <= b.expected_loss + len(reasons)
        # маркетинг читает долю ожидаемой потери, а не ранжирующий score
        assert b.for_money().risk_score == pytest.approx(100 * expected / b.horizon_revenue)
        # горизонт по умолчанию 30 дней, поэтому месячный отток равен доле за горизонт
        assert b.detail["marketing"]["monthly_churn"] == pytest.approx(expected / b.horizon_revenue)


def test_challenger_probability_is_logistic():
    table = calibration.tables()
    assert table is not None
    model = table["challenger"]
    nothing = calibration.challenger_probability({})
    assert nothing == pytest.approx(1 / (1 + pow(2.718281828459045, -model["intercept"])), rel=1e-6)
    strongest = max(model["coef"], key=lambda code: model["coef"][code])
    assert calibration.challenger_probability({strongest: 1.0}) > nothing


def test_matches_settings():
    assert calibration.matches(merge_settings(None)) is True
    changed = merge_settings({"weights": {"suspended": {"weight": 99, "enabled": True}}})
    assert calibration.matches(changed) is False


# --- дрейф --------------------------------------------------------------------------------------


def test_psi_zero_for_same_distribution_and_grows_with_shift():
    base = [0.7, 0.1, 0.1, 0.05, 0.05]
    assert drift.psi(base, base) == pytest.approx(0.0)
    small = drift.psi([0.65, 0.12, 0.11, 0.06, 0.06], base)
    big = drift.psi([0.2, 0.1, 0.1, 0.3, 0.3], base)
    assert 0 < small < drift.WARN < drift.ALERT < big


def test_psi_survives_empty_bins():
    assert drift.psi([10, 0, 0, 0, 0], [0, 0, 0, 0, 10]) > drift.ALERT


def test_bins():
    assert [drift.bin_of(v) for v in (0.0, 0.1, 0.25, 0.26, 0.5, 0.74, 1.0)] == [0, 1, 1, 2, 2, 3, 4]


def test_feature_psi_needs_enough_services(sample_data, settings):
    accounts, services, transactions = parse_snapshot(sample_data)
    results = score_customers(accounts, services, transactions, settings, AS_OF)
    report = drift.feature_psi(results, settings)
    assert report["status"] == "too_few"
    assert report["features"] == {}


def test_feature_psi_on_reference_like_base(settings):
    """База, где у всех одна и та же картина, сильно отличается от эталона — PSI это видит."""
    accounts = [make_account(id=i, balance=0, total_replenished=1_000) for i in range(1, 301)]
    services = [
        make_service(id=i, owner_id=i, status="prolong_wait", expires_in_days=-1, auto_prolong=False)
        for i in range(1, 301)
    ]
    results = score_customers(accounts, services, [], settings, AS_OF)
    report = drift.feature_psi(results, settings)
    assert report["services"] == 300
    assert report["status"] == "alert"
    assert report["features"]["prolong_wait"] > drift.ALERT
