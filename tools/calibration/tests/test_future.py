from datetime import UTC, datetime, timedelta

import pytest

from tools.calibration import future
from tools.calibration._deps import generator

REF = datetime(2026, 10, 1, 12, tzinfo=UTC)
NEVER_PAYS = future.Behavior(manual=0.0, topup=0.0, wait_pay=0.0, cancel=0.0)


def _account(balance: int) -> dict:
    return {
        "id": 1,
        "email": "a@example.test",
        "balance": balance,
        "withdrawBalance": 0,
        "totalReplenished": balance,
        "bonusBalance": 0,
        "bonusState": "locked",
    }


def _service(**overrides) -> dict:
    service = {
        "id": 1,
        "name": "vps-00001",
        "ownerId": 1,
        "paymentTerm": "week",
        "autoProlong": True,
        "createdAt": "2026-09-28T12:00:00.000Z",
        "expiresAt": "2026-10-05T12:00:00.000Z",
        "status": "active",
        "price": 500,
    }
    service.update(overrides)
    return service


def _snapshot(balance: int, **service) -> dict:
    return {
        "reference_time": "2026-10-01T12:00:00.000Z",
        "accounts": [_account(balance)],
        "services": [_service(**service)],
        "transactions": [],
    }


def test_segment_recording_does_not_change_dataset():
    plain = generator.generate(300, 7, REF)
    data, segments = future.generate_with_segments(300, 7, REF)
    assert data == plain
    assert generator._Builder.__name__ == "_Builder"
    assert set(segments) == {s["ownerId"] for s in data["services"]}
    assert set(segments.values()) <= {"loyal", "risky", "churning"}


def test_stream_changes_per_tick_matches_mean():
    # 1200 услуг * 0.2 в сутки / 24 = 10 изменений за час, randint(1, 19) даёт в среднем 10.
    assert future.stream_changes_per_tick(1200, 0.2, timedelta(hours=1)) == 19
    assert future.stream_changes_per_tick(1, 0.01, timedelta(hours=1)) == 1


def test_simulations_are_deterministic_and_cover_live_services():
    data, segments = future.generate_with_segments(200, 3, REF)
    live = {s["id"] for s in data["services"] if s["status"] in future.LIVE}
    a = future.simulate_stream(data, seed=1, horizon_days=10, changes_per_service_day=0.2)
    b = future.simulate_stream(data, seed=1, horizon_days=10, changes_per_service_day=0.2)
    assert a.final_status == b.final_status
    assert set(a.final_status) == live
    kwargs = {"seed": 1, "horizon_days": 10, "background_per_service_day": 0.02, "concentration": 8}
    c = future.simulate_calendar(data, segments, **kwargs)
    d = future.simulate_calendar(data, segments, **kwargs)
    assert c.final_status == d.final_status
    assert set(c.final_status) == live
    # Симуляция работает на копиях и исходный набор не меняет.
    assert data == future.generate_with_segments(200, 3, REF)[0]


@pytest.fixture
def never_paying_loyal(monkeypatch):
    monkeypatch.setitem(future.SEGMENT_BEHAVIOR, "loyal", NEVER_PAYS)


def test_calendar_deletes_unpaid_waiting_service_after_grace(never_paying_loyal):
    data = _snapshot(
        0,
        status="prolong_wait",
        autoProlong=False,
        paymentTerm="month",
        createdAt="2026-08-30T12:00:00.000Z",
        expiresAt="2026-09-30T12:00:00.000Z",
    )
    kwargs = {"seed": 1, "background_per_service_day": 0, "concentration": 0}
    early = future.simulate_calendar(data, {1: "loyal"}, horizon_days=3, **kwargs)
    late = future.simulate_calendar(data, {1: "loyal"}, horizon_days=5, **kwargs)
    assert early.final_status[1] == "prolong_wait"
    assert late.final_status[1] == "deleted"


def test_calendar_autoprolong_without_money_goes_to_wait(never_paying_loyal):
    data = _snapshot(0)
    result = future.simulate_calendar(
        data, {1: "loyal"}, seed=1, horizon_days=30, background_per_service_day=0, concentration=0
    )
    assert result.events["auto_failed"] == 1
    assert result.final_status[1] == "deleted"


def test_calendar_autoprolong_with_money_renews(monkeypatch):
    monkeypatch.setitem(future.SEGMENT_BEHAVIOR, "loyal", future.Behavior(1.0, 1.0, 1.0, 0.0))
    data = _snapshot(10_000)
    result = future.simulate_calendar(
        data, {1: "loyal"}, seed=1, horizon_days=30, background_per_service_day=0, concentration=0
    )
    assert result.final_status[1] == "active"
    assert result.events["auto_renew"] == 4
