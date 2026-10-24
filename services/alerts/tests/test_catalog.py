"""Каталог событий и разбор персональных настроек."""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from app.catalog import (
    EVENTS,
    PrefsError,
    default_prefs,
    in_quiet_hours,
    parse_prefs,
    prefs_from_row,
    prefs_to_json,
    prefs_to_storage,
)


def test_defaults_are_quiet_by_design() -> None:
    prefs = default_prefs("anna")
    # все критические события, кроме «данные без пересчёта», включены, но только в панели
    assert {code for code, p in prefs.events.items() if p.enabled} == {
        "loss_growth",
        "big_customer_high",
        "risk_share",
        "high_count_growth",
    }
    assert all(p.channels == ("panel",) for p in prefs.events.values())
    assert not prefs.digest_enabled
    # поклиентского «перешёл в высокий риск» в каталоге больше нет — только крупные клиенты
    assert "high_risk" not in {e.code for e in EVENTS}


def test_parse_and_roundtrip() -> None:
    body = {
        "email": " anna@example.com ",
        "timezone": "Europe/Moscow",
        "quiet_from": "22:00",
        "quiet_to": "08:30",
        "digest_enabled": True,
        "digest_time": "09:15",
        "digest_channels": ["email", "telegram"],
        "events": {
            "loss_growth": {"enabled": True, "threshold": 15, "period_h": 6, "channels": ["panel", "email"]},
            "big_customer_high": {"enabled": False, "threshold": 1000, "channels": []},
        },
    }
    prefs = parse_prefs("anna", body)
    assert prefs.email == "anna@example.com"
    assert prefs.quiet_from == time(22, 0) and prefs.quiet_to == time(8, 30)
    assert prefs.event("loss_growth").period_h == 6
    assert prefs.event("loss_growth").channels == ("panel", "email")
    assert not prefs.event("big_customer_high").enabled
    # не переданные события — по умолчанию
    assert prefs.event("risk_share").threshold == 40

    row = {**prefs_to_json(prefs), "events": prefs_to_storage(prefs)}
    row.update(quiet_from=prefs.quiet_from, quiet_to=prefs.quiet_to, digest_time=prefs.digest_time)
    again = prefs_from_row(row)
    assert again.events == prefs.events and again.digest_channels == ("email", "telegram")


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"email": "не почта"}, "email"),
        ({"timezone": "Mars/Base"}, "timezone"),
        ({"quiet_from": "22:00"}, "quiet_to"),
        ({"digest_time": "25:99"}, "digest_time"),
        ({"events": {"loss_growth": {"threshold": 0}}}, "events.loss_growth.threshold"),
        ({"events": {"loss_growth": {"period_h": 5}}}, "events.loss_growth.period_h"),
        ({"events": {"risk_share": {"channels": ["sms"]}}}, "events.risk_share.channels"),
        ({"events": {"nope": {}}}, "events.nope"),
        # письма без адреса
        ({"events": {"risk_share": {"channels": ["email"]}}}, "email"),
    ],
)
def test_validation(body: dict, field: str) -> None:
    with pytest.raises(PrefsError) as info:
        parse_prefs("anna", body)
    assert field in [d["field"] for d in info.value.details]


def test_quiet_hours_across_midnight() -> None:
    prefs = parse_prefs("anna", {"timezone": "Europe/Moscow", "quiet_from": "22:00", "quiet_to": "08:00"})
    # 23:30 по Москве = 20:30 UTC — тихо до 08:00 по Москве (05:00 UTC)
    end = in_quiet_hours(prefs, datetime(2026, 10, 24, 20, 30, tzinfo=UTC))
    assert end is not None and end.astimezone(UTC) == datetime(2026, 10, 25, 5, 0, tzinfo=UTC)
    # 07:00 по Москве — ещё тихо, конец сегодня
    end = in_quiet_hours(prefs, datetime(2026, 10, 24, 4, 0, tzinfo=UTC))
    assert end is not None and end.astimezone(UTC) == datetime(2026, 10, 24, 5, 0, tzinfo=UTC)
    # 12:00 по Москве — не тихо
    assert in_quiet_hours(prefs, datetime(2026, 10, 24, 9, 0, tzinfo=UTC)) is None
    assert in_quiet_hours(default_prefs("x"), datetime(2026, 10, 24, 20, 30, tzinfo=UTC)) is None
