"""Проверки критических событий по порогам и тексты: письмо, Telegram, деньги."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.catalog import EventPref
from app.messages import email_payload, public_link_ok, telegram_payload
from app.notify import (
    Snapshot,
    Transition,
    baseline_time,
    check_big_customers,
    check_high_count,
    check_loss_growth,
    check_risk_share,
    check_stale,
    digest_notice,
    period_label,
)
from app.rules import feed_path, money

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


def snap(loss: int = 100_000, high: int = 10, share: float = 0.2, customers: int = 100) -> Snapshot:
    return Snapshot(NOW, loss, high, share, customers)


def pref(threshold: float, period_h: int | None = 24) -> EventPref:
    return EventPref(enabled=True, threshold=threshold, period_h=period_h, channels=("panel",))


def test_money_and_periods() -> None:
    assert money(4_550) == "€45,50"
    assert money(123_456_789) == "€1 234 568"
    assert period_label(24) == "сутки" and period_label(72) == "3 дня" and period_label(6) == "6 часов"


def test_loss_growth_strictly_above_threshold() -> None:
    assert check_loss_growth(snap(110_000), snap(100_000), pref(10)) is None
    notice = check_loss_growth(snap(112_500), snap(100_000), pref(10))
    assert notice is not None
    assert notice.title == "Ожидаемая потеря выросла на 12,5 % за сутки"
    assert "Было €1 000, стало €1 125" in notice.text
    assert check_loss_growth(snap(), None, pref(10)) is None


def test_baseline_not_before_last_notification() -> None:
    assert baseline_time(NOW, pref(10, 24), None) == NOW - timedelta(hours=24)
    sent = NOW - timedelta(hours=2)
    assert baseline_time(NOW, pref(10, 24), sent) == sent


def test_high_count_and_risk_share() -> None:
    assert check_high_count(snap(high=20), snap(high=10), pref(10)) is None
    notice = check_high_count(snap(high=21), snap(high=10), pref(10))
    assert notice is not None and notice.link == "/customers?level=high"
    # только пересечение порога снизу вверх
    assert check_risk_share(snap(share=0.45), snap(share=0.39), pref(40, None)) is not None
    assert check_risk_share(snap(share=0.50), snap(share=0.45), pref(40, None)) is None
    assert check_risk_share(snap(share=0.45), None, pref(40, None)) is None


def test_big_customers_one_notice_per_pass() -> None:
    small = Transition(1, "a@x.io", 10_000, "medium")
    big = Transition(2, "b@x.io", 90_000, "medium")
    assert check_big_customers([small], pref(500, None)) is None
    single = check_big_customers([small, big], pref(500, None))
    assert single is not None
    assert single.title == "Крупный клиент #2 (b@x.io) перешёл в высокий риск" and single.link == "/customers/2"
    many = [Transition(i, None, 60_000 + i, None) for i in range(10)]
    notice = check_big_customers(many, pref(500, None))
    assert notice is not None and notice.title == "10 крупных клиентов перешли в высокий риск"
    assert notice.link == "/customers?level=high&sort=expected_loss&order=desc"
    assert len(notice.lines) == 6 and notice.lines[-1] == "…и ещё 5"


def test_stale() -> None:
    change = NOW - timedelta(minutes=30)
    assert check_stale(change, change - timedelta(minutes=1), NOW, pref(15, None)) is not None
    assert check_stale(change, change + timedelta(seconds=5), NOW, pref(15, None)) is None  # пересчитали
    assert check_stale(NOW - timedelta(minutes=5), None, NOW, pref(15, None)) is None  # ещё рано


def test_digest_and_renderers() -> None:
    notice = digest_notice(snap(loss=150_000), snap(loss=100_000), [Transition(7, "c@x.io", 50_000, "high")], 3)
    assert "Ожидаемая потеря: €1 500 (+€500 за сутки)" in notice.lines
    mail = email_payload(notice, "http://localhost:8080")
    assert mail["subject"] == "ChurnRadar: Сводка ChurnRadar за сутки"
    assert "http://localhost:8080/customers?level=high&amp;sort=expected_loss&amp;order=desc" in mail["html"]
    assert "Настроить уведомления: http://localhost:8080/notifications" in mail["text"]
    assert "<script" not in email_payload(notice.__class__("t", "<script>", "x", "/"), "http://localhost:8080")["html"]

    # Telegram не принимает кнопку со ссылкой на localhost: ссылка уходит в текст
    local = telegram_payload(notice, "http://localhost:8080")
    assert "button" not in local and "http://localhost:8080/customers" in local["text"]
    public = telegram_payload(notice, "https://churn.example.com")
    assert public["button"]["url"].startswith("https://churn.example.com/customers")
    assert public_link_ok("https://churn.example.com/x") and not public_link_ok("http://127.0.0.1:8080/")


def test_feed_paths() -> None:
    assert feed_path("big_customer_high", 5) == "/customers/5"
    assert feed_path("big_customer_high", None) == "/customers?level=high&sort=expected_loss&order=desc"
    assert feed_path("loss_growth", None) == "/"
    assert feed_path("stale_data", None) == "/data"
    assert feed_path("high_count_growth", None) == "/customers?level=high"
