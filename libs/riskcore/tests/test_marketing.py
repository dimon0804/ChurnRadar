"""Маркетинговые метрики: формулы на ручных примерах и вся база кейса, посчитанная руками."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from riskcore import (
    CustomerResult,
    ServiceOutcome,
    StatusChange,
    apply_economics,
    build_marketing,
    build_summary,
    default_settings,
    parse_snapshot,
    scale_customer,
    score_customers,
)
from riskcore.marketing import (
    LIFETIME_CAP_MONTHS,
    RETENTION_HORIZON_MONTHS,
    exit_moment,
    expected_lifetime,
    ltv,
    ltv_to_cac,
    monthly_rate,
    payback_months,
    payments_of,
    revenue_over,
)

from .factories import AS_OF, make_service, make_tx

# --- формулы -----------------------------------------------------------------------------------


def test_monthly_rate_converts_horizon_to_30_days():
    # за 60 дней уходит 19% → за 30 дней 1 − √0,81 = 10%
    assert monthly_rate(0.19, 60) == pytest.approx(0.10)
    # горизонт 30 дней — пересчитывать нечего
    assert monthly_rate(0.42, 30) == pytest.approx(0.42)
    # за 15 дней 10% → за 30 дней 1 − 0,9² = 19%
    assert monthly_rate(0.10, 15) == pytest.approx(0.19)
    assert monthly_rate(1.0, 7) == 1.0
    assert monthly_rate(-0.5, 30) == 0.0


def test_lifetime_is_inverse_churn_with_cap():
    assert expected_lifetime(0.10) == (pytest.approx(10.0), False)
    assert expected_lifetime(0.5) == (pytest.approx(2.0), False)
    # при оттоке 1% формула дала бы 100 месяцев — ограничиваем тремя годами
    assert LIFETIME_CAP_MONTHS == 36
    assert expected_lifetime(0.01) == (LIFETIME_CAP_MONTHS, True)
    assert expected_lifetime(0.03) == (pytest.approx(100 / 3), False)  # 33 мес. — ещё под потолком
    assert expected_lifetime(0.0) == (LIFETIME_CAP_MONTHS, True)


def test_ltv_payback_and_ratio_by_hand():
    # €10 в месяц, отток 10% → живёт 10 месяцев → €100 выручки, при марже 40% — €40 прибыли
    assert ltv(1_000, 0.10) == pytest.approx(10_000)
    assert ltv(1_000, 0.10, margin=0.4) == pytest.approx(4_000)
    # привлечение €20, клиент приносит €10 × 40% = €4 в месяц → окупится за 5 месяцев
    assert payback_months(2_000, 1_000, 0.4) == pytest.approx(5.0)
    assert ltv_to_cac(4_000, 2_000) == pytest.approx(2.0)
    assert payback_months(None, 1_000, 1.0) is None
    assert ltv_to_cac(4_000, None) is None
    assert payback_months(2_000, 0, 1.0) is None


def test_payments_skip_failed_and_zero_amounts_in_count():
    services = [make_service(id=1, created_days_ago=100), make_service(id=2, created_days_ago=10)]
    txs = [
        make_tx(1, service_id=1, amount=500, days_ago=120),
        make_tx(2, service_id=1, amount=700, status="not_performed"),
        make_tx(3, service_id=2, amount=0, type="order"),
        make_tx(4, service_id=2, amount=300),
    ]
    paid = payments_of(services, txs)
    assert paid.total == 800
    assert paid.count == 2  # нулевой заказ — не платёж для среднего чека
    assert paid.first_seen == AS_OF - timedelta(days=120)  # транзакция раньше услуги


def test_exit_moment_uses_history_then_estimate():
    gone = [
        make_service(id=1, status="deleted", expires_in_days=-40),
        make_service(id=2, status="blocked", expires_in_days=-20),
    ]
    # без истории: окончание оплаты + 5 дней, берём самую позднюю услугу
    assert exit_moment(gone, AS_OF) == AS_OF - timedelta(days=15)
    # история знает точную дату
    assert exit_moment(gone, AS_OF, {2: AS_OF - timedelta(days=3)}) == AS_OF - timedelta(days=3)
    # не позже опорного момента
    assert exit_moment([make_service(status="blocked", expires_in_days=90)], AS_OF) == AS_OF
    assert exit_moment([*gone, make_service(id=3)], AS_OF) is None
    assert exit_moment([], AS_OF) is None


# --- агрегаты на собранной вручную базе ---------------------------------------------------------


def _result(
    cid: int,
    level: str,
    score: float | None,
    monthly: float,
    services: list[tuple[str, bool, str, float]],
    paid: int = 0,
    payments: int = 0,
    days_with_us: float = 300.0,
) -> CustomerResult:
    """Результат клиента без модели: score и выручка заданы руками. services — (срок, автопродление,
    статус, месячная выручка услуги)."""
    outcomes = []
    for i, (term, auto, status, svc_monthly) in enumerate(services):
        live = status in ("active", "suspended", "prolong_wait")
        service = make_service(
            id=cid * 10 + i,
            owner_id=cid,
            payment_term=term,
            auto_prolong=auto,
            status=status,  # type: ignore[arg-type]
            expires_in_days=30 if live else -60,
        )
        outcomes.append(
            ServiceOutcome(
                service=service,
                days_to_expiry=0.0,
                monthly_price=svc_monthly,
                revenue=svc_monthly if live else 0.0,
                probability=(score or 0.0) / 100 if live else None,
                level=level if live else "unknown",  # type: ignore[arg-type]
            )
        )
    return CustomerResult(
        customer_id=cid,
        email=f"c{cid}@example.test",
        balance=0,
        bonus_balance=0,
        bonus_state="locked",
        withdraw_balance=0,
        total_replenished=0,
        risk_score=score,
        risk_level=level,  # type: ignore[arg-type]
        loyalty=None,
        reasons=[],
        services_total=0,
        services_at_risk=0,
        monthly_revenue=round(monthly),
        expected_loss=0,
        exposure=0,
        detail={},
        model_version=1,
        as_of=AS_OF,
        horizon_revenue=monthly,
        services=tuple(outcomes),
        paid_total=paid,
        payments=payments,
        first_seen=AS_OF - timedelta(days=days_with_us),
    )


@pytest.fixture
def base():
    # горизонт 30 дней: доля за горизонт и за месяц совпадают, всё считается в уме
    results = [
        # €100/мес, уйдёт с вероятностью 50% → живёт 2 мес. → LTV €200
        _result(1, "high", 50.0, 10_000, [("month", True, "active", 10_000)], paid=30_000, payments=3),
        # €40/мес, 20% → 5 мес. → LTV €200
        _result(2, "medium", 20.0, 4_000, [("year", False, "active", 4_000)], paid=12_000, payments=1),
        # €60/мес, 10% → 10 мес. → LTV €600
        _result(
            3,
            "low",
            10.0,
            6_000,
            [("month", True, "active", 2_000), ("week", False, "active", 4_000)],
            paid=6_000,
            payments=2,
        ),
        # ушёл 60 дней назад + 5 дней ожидания: всё удалено, платил €50
        _result(
            4, "unknown", None, 0, [("month", True, "deleted", 0)], paid=5_000, payments=1, days_with_us=360
        ),
    ]
    return build_marketing(results, [], 30, AS_OF)


def test_revenue_and_arpu_by_hand(base):
    assert base["mrr"] == 20_000
    assert base["customers_total"] == 4
    assert base["active_customers"] == 3
    assert base["paying_customers"] == 3
    assert base["arpu"] == round(20_000 / 3)
    assert base["arppu"] == round(20_000 / 3)


def test_forecast_churn_by_hand(base):
    churn = base["churn"]
    # клиенты: (0,5 + 0,2 + 0,1) / 3; выручка: (100×0,5 + 40×0,2 + 60×0,1) / 200 = 64 / 200
    assert churn["customer_monthly"] == pytest.approx(0.8 / 3)
    assert churn["revenue_monthly"] == pytest.approx(0.32)
    assert churn["expected_mrr_loss"] == 6_400
    assert churn["expected_customers_lost"] == pytest.approx(0.8)
    assert base["retention"]["customer_monthly"] == pytest.approx(1 - 0.8 / 3)
    assert base["retention"]["revenue_monthly"] == pytest.approx(0.68)


def test_ltv_by_hand(base):
    # средний клиент: ARPU €66,67 / отток 26,67% = 3,75 мес. → €250
    assert base["lifetime"]["expected_months"] == pytest.approx(3.75)
    assert base["ltv"] == 25_000
    # по клиентам: 200 + 200 + 600
    assert base["ltv_total"] == 100_000
    assert base["ltv_at_risk"] == 40_000
    assert base["ltv_fact"] == {
        "total": 53_000,
        "avg_per_customer": 13_250,
        "payments": 7,
        "avg_check": 7_571,
    }


def test_fact_churn_by_hand(base):
    fact = base["churn"]["fact"]
    # трое с нами по 300 дней, ушедший прожил 360 − 55 = 305 дней: (900 + 305) / 30 клиенто-месяцев
    assert fact["customers_left"] == 1
    assert fact["exposure_months"] == pytest.approx(1205 / 30, abs=0.01)
    assert fact["monthly_churn"] == pytest.approx(30 / 1205)
    assert fact["avg_tenure_months"] == pytest.approx(1205 / 30 / 4)
    assert fact["avg_lifetime_left_months"] == pytest.approx(305 / 30)


def test_structure_by_hand(base):
    # топ-10% из трёх платящих — один клиент, €100 из €200
    assert base["concentration"] == {"top_customers": 1, "top_share": pytest.approx(0.5)}
    # автопродление у клиентов 1 и 3; по выручке — €100 + €20 из €200
    assert base["auto_prolong"] == {
        "customers": 2,
        "customers_share": pytest.approx(2 / 3),
        "mrr_share": pytest.approx(0.6),
    }
    terms = {row["term"]: row for row in base["payment_terms"]}
    assert terms["month"]["services"] == 2 and terms["month"]["mrr"] == 12_000
    assert terms["week"]["mrr"] == 4_000 and terms["year"]["mrr"] == 4_000
    assert terms["hour"]["services"] == 0 and terms["hour"]["share"] == 0


def test_segments_add_up(base):
    rows = {row["level"]: row for row in base["segments"]}
    assert rows["high"] == {
        "level": "high",
        "customers": 1,
        "mrr": 10_000,
        "arpu": 10_000,
        "monthly_churn": pytest.approx(0.5),
        "ltv": 20_000,
        "ltv_avg": 20_000,
        "expected_mrr_loss": 5_000,
    }
    assert rows["unknown"]["customers"] == 1 and rows["unknown"]["mrr"] == 0
    assert sum(r["mrr"] for r in rows.values()) == base["mrr"]
    assert sum(r["expected_mrr_loss"] for r in rows.values()) == base["churn"]["expected_mrr_loss"]
    assert sum(r["ltv"] for r in rows.values()) == base["ltv_total"]


def test_revenue_over_twelve_months_by_hand():
    assert RETENTION_HORIZON_MONTHS == 12
    # без оттока — ровно 12 месячных платежей
    assert revenue_over(1_000, 0.0) == pytest.approx(12_000)
    # отток 50%: 1 + 0,5 + 0,25 + … ≈ 2 месяца выручки
    assert revenue_over(1_000, 0.5) == pytest.approx(1_000 * (1 - 0.5**12) / 0.5)
    # уходит наверняка — платит только первый месяц
    assert revenue_over(1_000, 1.0) == pytest.approx(1_000)


def test_retention_scenario_targets_low_risk_churn_over_a_year(base):
    high, at_risk = base["retention_scenarios"]
    # отток низкого риска — 10% в месяц; исторический отток (2,5%) больше не берём
    assert high["target_source"] == "low_risk"
    assert high["target_monthly_churn"] == pytest.approx(0.10)
    assert high["horizon_months"] == 12
    now = 10_000 * (1 - 0.5**12) / 0.5
    kept = 10_000 * (1 - 0.9**12) / 0.1
    assert high["revenue_now"] == round(now)
    assert high["revenue_retained"] == round(kept)
    assert high["revenue_uplift"] == round(kept) - round(now)
    assert high["saved_mrr"] == round(10_000 * (0.5 - 0.1))
    assert at_risk["levels"] == ["high", "medium"] and at_risk["customers"] == 2
    medium_gain = 4_000 * ((1 - 0.9**12) / 0.1 - (1 - 0.8**12) / 0.2)
    assert at_risk["revenue_uplift"] == pytest.approx(kept - now + medium_gain, abs=2)


def test_retention_uplift_is_plausible_not_lifetime():
    """Жюри: «LTV вырастет ×45». Год выручки при оттоке низкого риска не может быть больше 12 MRR."""
    results = [
        _result(1, "high", 60.0, 8_400, [("month", True, "active", 8_400)]),
        _result(2, "low", 0.3, 5_000, [("month", True, "active", 5_000)]),
    ]
    high, _ = build_marketing(results, [], 30, AS_OF)["retention_scenarios"]
    assert high["revenue_retained"] <= 12 * 8_400
    assert high["revenue_uplift"] < 12 * 8_400 - high["revenue_now"] + 1


def test_retention_falls_back_to_base_then_none():
    results = [
        _result(1, "high", 50.0, 10_000, [("month", True, "active", 10_000)]),
        _result(2, "medium", 20.0, 2_000, [("month", True, "active", 2_000)]),
    ]
    high, _ = build_marketing(results, [], 30, AS_OF)["retention_scenarios"]
    # низкого риска нет — средний отток по базе (50% + 20%) / 2
    assert high["target_source"] == "base"
    assert high["target_monthly_churn"] == pytest.approx(0.35)
    kept = round(10_000 * (1 - 0.65**12) / 0.35)
    assert high["revenue_uplift"] == kept - round(10_000 * (1 - 0.5**12) / 0.5)

    empty = build_marketing([], [], 30, AS_OF)["retention_scenarios"][0]
    assert empty["target_source"] == "none"
    assert empty["revenue_uplift"] is None and empty["revenue_retained"] is None


def test_history_dates_override_estimate():
    results = [_result(4, "unknown", None, 0, [("month", True, "deleted", 0)], days_with_us=100)]
    history = [StatusChange(40, 4, "prolong_wait", "deleted", AS_OF - timedelta(days=10))]
    fact = build_marketing(results, history, 30, AS_OF)["churn"]["fact"]
    assert fact["exposure_months"] == pytest.approx(90 / 30, abs=0.01)


def test_horizon_converted_to_month():
    results = [_result(1, "high", 19.0, 6_000, [("month", True, "active", 6_000)])]
    # горизонт 60 дней: 19% за горизонт = 10% в месяц, срок 10 мес.
    base = build_marketing(results, [], 60, AS_OF)
    assert base["churn"]["customer_horizon"] == pytest.approx(0.19)
    assert base["churn"]["customer_monthly"] == pytest.approx(0.10)
    assert base["mrr"] == 3_000  # horizon_revenue за 60 дней → 30 дней
    assert base["ltv"] == 30_000


def test_empty_base_has_all_fields():
    base = build_marketing([], [], 30, AS_OF)
    assert base["mrr"] == 0 and base["ltv"] == 0
    assert base["churn"]["customer_monthly"] is None
    assert base["lifetime"]["expected_months"] is None
    assert base["concentration"]["top_share"] is None
    out = apply_economics(base, 0.5, None)
    assert out["unit_economics"] == {"margin": 0.5, "cac": None, "ltv_cac": None, "payback_months": None}


def test_margin_and_cac_applied_on_top(base):
    out = apply_economics(base, margin=0.4, cac=5_000)
    assert out["ltv"] == 10_000  # €250 × 40%
    assert out["ltv_total"] == 40_000
    assert out["segments"][0]["ltv"] == 8_000
    # прирост выручки за год маржой не масштабируется: это выручка, так и подписано
    assert out["retention_scenarios"] == base["retention_scenarios"]
    economics = out["unit_economics"]
    assert economics["ltv_cac"] == pytest.approx(2.0)
    # €50 / (€66,67 × 40%) = 1,875 мес.
    assert economics["payback_months"] == pytest.approx(5_000 / (base["arpu"] * 0.4))
    # базовые числа не тронуты
    assert base["ltv"] == 25_000 and "unit_economics" not in base


def test_scale_customer():
    block = {"mrr": 1_000, "ltv_forecast": 5_000}
    assert scale_customer(block, 0.3) == {"mrr": 1_000, "ltv_forecast": 1_500, "margin": 0.3}


# --- база кейса: 10 клиентов, 30 услуг, 100 транзакций ------------------------------------------

# Посчитано руками по data/sample/data.json: проведённые транзакции по клиентам
# (сумма, число ненулевых) и дата первой услуги или транзакции.
CASE_PAID = {1: 3340, 2: 3332, 3: 2927, 4: 3401, 5: 3164, 6: 2373, 7: 4509, 8: 3986, 9: 2624, 10: 2831}
CASE_PAYMENTS = 66
# Ушли (все услуги удалены или заблокированы) клиенты 2, 3, 7 и 8. Дни с нами:
# 1 и 6 — услуга создана за 30 минут до опорного момента; 2 и 7 — заблокированы, услуга
# с оплатой до 2027 года, поэтому уход оценивается опорным моментом; 3 — последняя услуга
# до 14.03.2026 + 5 дней; 8 — до 14.09.2026 + 5 дней.
CASE_DAYS = {1: 1 / 48, 2: 49, 3: 186, 4: 88, 5: 400, 6: 1 / 48, 7: 32, 8: 370, 9: 82, 10: 400}
CASE_LEFT = {2, 3, 7, 8}


@pytest.fixture(scope="module")
def case(sample_data):
    accounts, services, transactions = parse_snapshot(sample_data)
    settings = default_settings()
    # как в scoring: маркетинг читает долю ожидаемой (калиброванной) потери, см. CustomerResult.for_money
    results = [r.for_money() for r in score_customers(accounts, services, transactions, settings, AS_OF)]
    return results, services, settings


def test_case_by_hand(case):
    results, services, settings = case
    base = build_marketing(results, [], settings.horizon_days, AS_OF)
    summary = build_summary(results, services, [], settings, AS_OF)

    # из модели берём только score и месячную выручку клиента, остальное — руками
    paying = {
        r.customer_id: (r.horizon_revenue, r.risk_score / 100) for r in results if r.risk_score is not None
    }
    assert sorted(paying) == [1, 4, 5, 6, 9, 10]
    mrr = sum(m for m, _ in paying.values())
    assert base["mrr"] == round(mrr) == summary["monthly_revenue_total"]
    assert base["active_customers"] == base["paying_customers"] == 6
    assert base["arpu"] == round(mrr / 6)

    churn = sum(p for _, p in paying.values()) / 6
    assert base["churn"]["customer_monthly"] == pytest.approx(churn)
    assert base["churn"]["revenue_monthly"] == pytest.approx(sum(m * p for m, p in paying.values()) / mrr)
    assert base["churn"]["revenue_monthly"] == pytest.approx(summary["revenue_at_risk_share"])
    assert base["ltv"] == round(mrr / 6 / churn)
    assert base["ltv_total"] == round(sum(m / p for m, p in paying.values()))
    assert base["concentration"]["top_customers"] == 1
    assert base["concentration"]["top_share"] == pytest.approx(max(m for m, _ in paying.values()) / mrr)

    assert base["ltv_fact"]["total"] == sum(CASE_PAID.values()) == 32_487
    assert base["ltv_fact"]["payments"] == CASE_PAYMENTS
    assert base["ltv_fact"]["avg_check"] == round(32_487 / 66) == 492

    fact = base["churn"]["fact"]
    exposure = sum(CASE_DAYS.values()) / 30
    assert fact["customers_left"] == len(CASE_LEFT)
    assert fact["exposure_months"] == pytest.approx(exposure, abs=0.01)
    assert fact["monthly_churn"] == pytest.approx(4 / exposure)

    # у всех шести активных есть услуга на автопродлении; по три живые услуги каждого срока
    assert base["auto_prolong"]["customers_share"] == 1.0
    assert {row["term"]: row["services"] for row in base["payment_terms"]} == dict.fromkeys(
        ("hour", "week", "month", "quarter_year", "half_year", "year"), 3
    )
    levels = {row["level"]: row["customers"] for row in base["segments"]}
    assert levels == summary["by_level"]


def test_case_customer_cards(case):
    results, _, settings = case
    for r in results:
        block = r.detail["marketing"]
        assert block["ltv_fact"] == CASE_PAID[r.customer_id]
        assert block["tenure_days"] == pytest.approx(CASE_DAYS[r.customer_id], abs=0.05)
        assert (block["left_at"] is not None) == (r.customer_id in CASE_LEFT)
        if r.risk_score is None:
            assert block["monthly_churn"] is None and block["ltv_forecast"] == 0
            continue
        p = r.risk_score / 100
        assert block["mrr"] == r.monthly_revenue
        assert block["monthly_churn"] == pytest.approx(p)
        assert block["ltv_forecast"] == round(r.horizon_revenue / p)
        assert math.isclose(block["expected_lifetime_months"], 1 / p, abs_tol=0.01)


def test_case_first_seen_matches_earliest_record(case, sample_data):
    results, *_ = case
    earliest: dict[int, str] = {}
    owner = {s["id"]: s["ownerId"] for s in sample_data["services"]}
    for s in sample_data["services"]:
        earliest[s["ownerId"]] = min(earliest.get(s["ownerId"], s["createdAt"]), s["createdAt"])
    for t in sample_data["transactions"]:
        cid = owner[t["serviceId"]]
        earliest[cid] = min(earliest[cid], t["createdAt"])
    for r in results:
        expected = datetime.fromisoformat(earliest[r.customer_id].replace("Z", "+00:00"))
        assert r.first_seen == expected
