"""Правила оповещений и тексты: чистые функции, без БД."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.rules import (
    Alert,
    Thresholds,
    TotalState,
    batch_text,
    evaluate_customers,
    evaluate_total,
    money,
    webhook_payload,
)

NOW = datetime(2026, 10, 19, 15, tzinfo=UTC)
TH = Thresholds(loss_jump_cents=5_000, total_loss_jump_pct=10.0, cooldown=timedelta(minutes=60))
URL = "https://churn.example"


def row(
    cid: int = 1,
    level: str = "medium",
    loss: int = 1_000,
    *,
    prev_level: str | None = "medium",
    prev_loss: int | None = 1_000,
    loss_ref: int | None = 1_000,
    last_alert_at: datetime | None = None,
) -> dict:
    return {
        "customer_id": cid,
        "email": f"u{cid}@example.com",
        "risk_level": level,
        "expected_loss": loss,
        "prev_level": prev_level,
        "prev_loss": prev_loss,
        "loss_ref": loss_ref,
        "last_alert_at": last_alert_at,
    }


def test_money() -> None:
    assert money(4_550) == "€45,50"
    assert money(12_000) == "€120"
    assert money(123_456_789) == "€1 234 568"
    assert money(-500) == "−€5"
    assert money(None) == "—"


def test_transition_to_high_raises_alert_and_remembers_time() -> None:
    d = evaluate_customers([row(level="high", loss=9_000)], TH, NOW)
    assert [a.kind for a in d.alerts] == ["high_risk"]
    alert = d.alerts[0]
    assert alert.prev_level == "medium" and alert.risk_level == "high"
    assert "перешёл в высокий риск (был средний)" in alert.message
    assert "€90" in alert.message
    assert d.states[0].last_alert_at == NOW
    assert d.states[0].loss_ref == 9_000


def test_staying_high_is_quiet() -> None:
    d = evaluate_customers([row(level="high", prev_level="high", loss=1_100)], TH, NOW)
    assert d.alerts == []
    assert d.states[0].risk_level == "high"


def test_new_customer_straight_into_high() -> None:
    d = evaluate_customers(
        [row(cid=7, level="high", loss=3_000, prev_level=None, prev_loss=None, loss_ref=None)], TH, NOW
    )
    assert len(d.alerts) == 1
    assert d.alerts[0].message.startswith("Новый клиент #7")
    # новый клиент в среднем риске — просто запоминаем
    d = evaluate_customers([row(cid=8, level="medium", prev_level=None, prev_loss=None, loss_ref=None)], TH, NOW)
    assert d.alerts == [] and d.states[0].loss_ref == 1_000


def test_loss_jump_strictly_more_than_threshold() -> None:
    assert evaluate_customers([row(loss=6_000)], TH, NOW).alerts == []  # ровно +€50 — не больше
    d = evaluate_customers([row(loss=6_001)], TH, NOW)
    assert [a.kind for a in d.alerts] == ["loss_jump"]
    assert d.alerts[0].prev_loss == 1_000
    assert "выросла на €50" in d.alerts[0].message


def test_slow_growth_counts_from_minimum() -> None:
    # потеря росла понемногу: в прошлый раз 4 000, отсчёт от 1 000 — сработает на 6 500
    d = evaluate_customers([row(loss=6_500, prev_loss=4_000, loss_ref=1_000)], TH, NOW)
    assert [a.kind for a in d.alerts] == ["loss_jump"]
    # падение опускает точку отсчёта
    d = evaluate_customers([row(loss=500, prev_loss=1_000, loss_ref=1_000)], TH, NOW)
    assert d.alerts == [] and d.states[0].loss_ref == 500


def test_cooldown_suppresses_but_updates_state() -> None:
    recent = NOW - timedelta(minutes=10)
    d = evaluate_customers([row(level="high", loss=9_000, last_alert_at=recent)], TH, NOW)
    assert d.alerts == []
    assert d.suppressed == {"high_risk": 1}
    assert d.states[0].risk_level == "high" and d.states[0].last_alert_at == recent
    # окно прошло — снова можно
    old = NOW - timedelta(minutes=61)
    assert len(evaluate_customers([row(level="high", loss=9_000, last_alert_at=old)], TH, NOW).alerts) == 1


def test_total_loss() -> None:
    alert, state, suppressed = evaluate_total(100_000, None, TH, NOW)
    assert alert is None and state == TotalState(100_000, 100_000, None) and not suppressed

    alert, state, _ = evaluate_total(110_000, state, TH, NOW)  # ровно 10 % — не больше
    assert alert is None and state.total_loss_ref == 100_000

    alert, state, _ = evaluate_total(111_000, state, TH, NOW)
    assert alert is not None and alert.kind == "total_loss_jump"
    assert "выросла на 11,0 %" in alert.message
    assert state == TotalState(111_000, 111_000, NOW)

    # рост сразу после оповещения — в окне анти-спама
    alert, state, suppressed = evaluate_total(200_000, state, TH, NOW + timedelta(minutes=5))
    assert alert is None and suppressed
    # падение опускает точку отсчёта
    _, state, _ = evaluate_total(90_000, state, TH, NOW + timedelta(minutes=6))
    assert state.total_loss_ref == 90_000


def test_batch_text_orders_and_truncates() -> None:
    alerts = [Alert(kind="loss_jump", customer_id=i, message=f"m{i}") for i in range(1, 14)]
    alerts.append(Alert(kind="total_loss_jump", message="итог"))
    text = batch_text(alerts, URL, max_items=3)
    lines = text.splitlines()
    assert lines[0] == "ChurnRadar: 14 оповещений"
    assert lines[1].startswith("• итог") and lines[1].endswith(f"{URL}/customers?level=high")
    assert lines[2] == f"• m1 {URL}/customers/1"
    assert lines[-1] == f"…и ещё 11: {URL}/customers?level=high"


def test_webhook_payload_shape() -> None:
    alert = Alert(
        kind="high_risk",
        customer_id=5,
        email="a@b.c",
        risk_level="high",
        prev_level="low",
        expected_loss=100,
        prev_loss=50,
        message="текст",
    )
    body = webhook_payload([alert], batch_id="b", event_ids=["e"], created_at=NOW, public_url=URL, max_items=10)
    assert body["type"] == "churn.alerts"
    assert body["created_at"] == "2026-10-19T15:00:00.000Z"
    assert body["text"].startswith("ChurnRadar: 1 оповещение")
    assert body["alerts"][0] == {
        "kind": "high_risk",
        "customer_id": 5,
        "email": "a@b.c",
        "risk_level": "high",
        "prev_level": "low",
        "expected_loss_cents": 100,
        "prev_loss_cents": 50,
        "message": "текст",
        "url": f"{URL}/customers/5",
    }
