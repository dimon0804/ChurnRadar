"""Действия для маркетолога: у каждой причины своё, у клиента одно главное."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from riskcore import calibration, merge_settings, noisy_or, score_customers
from riskcore.money import format_eur, offer_size
from riskcore.text import format_deadline, format_funds

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
    [result] = score_customers([account], [service], [], merge_settings({"calibrated": False}), AS_OF)
    [outcome] = result.services
    totals = {t.code: t for t in result.feature_totals}
    revenue = outcome.revenue
    p = noisy_or([0.35, 0.105])
    assert totals["suspended"].saving == pytest.approx(revenue * (p - 0.105))
    assert totals["short_commitment"].saving == pytest.approx(revenue * (p - 0.35))
    assert result.detail["next_action"]["code"] == "suspended"
    assert result.detail["next_action"]["service_id"] == service.id


def test_saving_with_calibration_compares_calibrated_probabilities():
    """С калибровкой эффект — разница калиброванных вероятностей «с причиной» и «без неё»."""
    account = make_account()
    service = make_service(status="suspended", payment_term="week", price=700, expires_in_days=60)
    [result] = score_customers([account], [service], [], merge_settings({"calibrated": True}), AS_OF)
    [outcome] = result.services
    totals = {t.code: t for t in result.feature_totals}
    q = calibration.calibrate
    p = noisy_or([0.35, 0.105])
    assert outcome.calibrated == pytest.approx(q(p, 30))
    assert totals["suspended"].saving == pytest.approx(outcome.revenue * (q(p, 30) - q(0.105, 30)))
    assert totals["short_commitment"].saving == pytest.approx(outcome.revenue * (q(p, 30) - q(0.35, 30)))


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
    # срочность — отдельный флаг по сроку, в тексте действия слова «Срочно» нет
    assert action["title"].startswith("Написать: service-0001")
    assert "через 3 дн." in action["title"]
    assert "Не хватает €40" in action["detail"]


def _promo_cents(text: str) -> int:
    match = re.search(r"промокод €([\d ]+)(?:,(\d\d))?", text)
    assert match, text
    return int(match.group(1).replace(" ", "")) * 100 + int(match.group(2) or 0)


def test_promo_is_capped_by_monthly_revenue_and_saving():
    """Жюри: клиенту с MRR €84 предлагали промокод на €436,48 — всю годовую нехватку."""
    account = make_account(balance=0, total_replenished=10_000)
    service = make_service(
        status="prolong_wait", auto_prolong=False, payment_term="year", price=43_648, expires_in_days=-1
    )
    [result] = score_customers([account], [service], [], merge_settings(None), AS_OF)
    action = result.detail["next_action"]
    assert action["code"] == "prolong_wait"
    monthly = result.horizon_revenue * 30 / 30
    saving = action["expected_saving"]
    promo = _promo_cents(action["detail"])
    assert 0 < promo <= monthly
    assert promo <= saving * 0.2
    # видно, окупается ли действие: «промокод €X сохранит ожидаемо €Y»
    assert f"Промокод {format_eur(promo)} сохранит ожидаемо {format_eur(saving)}" in action["detail"]
    assert "промокод €436" not in action["detail"]


def test_offer_size_rule():
    # хотели €436, клиент платит €84 в месяц, действие сбережёт €300 → не больше €60 (20%)
    assert offer_size(43_648, 8_400, 30_000) == 6_000
    # MRR меньше 20% сбережения → потолок — месяц выручки
    assert offer_size(43_648, 2_550, 30_000) == 2_500
    # просили немного — даём сколько просили, вниз до евро
    assert offer_size(1_250, 8_400, 30_000) == 1_200
    assert offer_size(5_000, 8_400, 300) == 0


def test_insufficient_funds_bonus_has_roi_and_is_capped():
    account = make_account(balance=100, total_replenished=3_000)
    services = [make_service(id=1, price=20_000, expires_in_days=3)]
    [result] = score_customers([account], services, [make_tx(1)], merge_settings(None), AS_OF)
    action = next(r for r in result.detail["recommendations"] if r["code"] == "insufficient_funds")
    assert action["title"] == "Нет денег на продление: напомнить пополнить баланс до 22.10.2026"
    match = re.search(r"бонус €(\d+)", action["detail"])
    assert match, action["detail"]
    bonus = int(match.group(1)) * 100
    assert bonus <= result.horizon_revenue and bonus <= action["expected_saving"] * 0.2
    assert f"бонус {format_eur(bonus)} сохранит ожидаемо" in action["detail"]


def test_overdue_renewal_says_deadline_passed_not_a_past_date():
    """Жюри: «пополнить баланс до 24.10.2026», когда этот момент уже прошёл."""
    account = make_account(balance=0, total_replenished=3_000)
    services = [make_service(id=1, price=2_000, expires_in_days=-0.5)]
    [result] = score_customers([account], services, [make_tx(1)], merge_settings(None), AS_OF)
    texts = [r["title"] for r in result.detail["recommendations"]] + [r["action"] for r in result.reasons]
    funds = [t for t in texts if t.startswith("Нет денег на продление")]
    assert funds, texts
    for text in funds:
        assert "срочно, срок уже наступил" in text
        assert "18.10.2026" not in text


def test_autoprolong_expired_has_no_past_deadline():
    account = make_account()
    services = [make_service(id=1, auto_prolong=False, expires_in_days=-3)]
    [result] = score_customers([account], services, [make_tx(1)], merge_settings(None), AS_OF)
    action = next(r for r in result.detail["recommendations"] if r["code"] == "autoprolong_off_expiring")
    assert action["title"].startswith("Включить автопродление: срочно, срок уже наступил")


def test_format_deadline_never_in_the_past():
    assert format_deadline(AS_OF + timedelta(days=2), AS_OF) == ("21.10.2026", False)
    assert format_deadline(AS_OF - timedelta(days=2), AS_OF) == ("19.10.2026", True)


def test_balance_in_explanations_names_bonuses():
    """Жюри: в карточке €43,32, в объяснении €49,47 — второе с бонусами, но об этом нигде не сказано."""
    assert format_funds(4_332, 615) == "€49,47 (вкл. бонусы €6,15)"
    assert format_funds(4_332, 0) == "€43,32"
    account = make_account(balance=4_332, bonus_balance=615, bonus_state="unlocked", total_replenished=3_000)
    services = [make_service(id=1, price=20_000, expires_in_days=3)]
    [result] = score_customers([account], services, [make_tx(1)], merge_settings(None), AS_OF)
    explanation = next(r["title"] for r in result.reasons if r["code"] == "insufficient_funds")
    assert "На балансе €49,47 (вкл. бонусы €6,15)" in explanation
