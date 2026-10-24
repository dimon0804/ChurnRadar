from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from riskcore import (
    AccountContext,
    AccountFeature,
    Signal,
    default_settings,
    feature_catalog,
    merge_settings,
    register,
    score_customers,
    settings_to_api,
)
from riskcore.registry import unregister

from .factories import AS_OF, make_account, make_service


@pytest.fixture
def extra_feature() -> Iterator[str]:
    @register
    class EmailOnTestDomain(AccountFeature):
        code = "test_domain_email"
        title = "Тестовый адрес"
        description = "Почта на тестовом домене"
        group = "behavior"
        default_weight = 25.0

        def compute(self, ctx: AccountContext) -> Signal | None:
            if ctx.account.email.endswith(".test"):
                return Signal(1.0, f"Адрес {ctx.account.email} на тестовом домене")
            return None

    yield EmailOnTestDomain.code
    unregister(EmailOnTestDomain.code)


def test_default_settings_cover_registry():
    s = default_settings()
    assert s.version == 1
    assert s.horizon_days == 30
    assert s.thresholds == {"medium": 35.0, "high": 65.0}
    assert set(s.weights) == {spec.code for spec in feature_catalog()}
    assert all(entry["enabled"] for entry in s.weights.values())


def test_merge_keeps_user_values_and_adds_new_feature(extra_feature):
    stored = {
        "horizon_days": 60,
        "thresholds": {"medium": 30, "high": 70},
        "weights": {
            "prolong_wait": {"weight": 99, "enabled": True},
            "suspended": {"weight": 12.5, "enabled": False},
            "removed_long_ago": {"weight": 50, "enabled": True},
        },
    }
    merged = merge_settings(stored, version=7)
    assert merged.version == 7
    assert merged.horizon_days == 60
    assert merged.thresholds == {"medium": 30.0, "high": 70.0}
    assert merged.weights["prolong_wait"] == {"weight": 99.0, "enabled": True}
    assert merged.weights["suspended"] == {"weight": 12.5, "enabled": False}
    assert merged.weights[extra_feature] == {"weight": 25.0, "enabled": True}
    assert merged.weights["inactivity"] == {"weight": 20.0, "enabled": True}
    assert "removed_long_ago" not in merged.weights


def test_new_feature_reaches_scores_and_api(extra_feature):
    settings = merge_settings({})
    [result] = score_customers(
        [make_account()], [make_service(payment_term="year", expires_in_days=200)], [], settings, AS_OF
    )
    codes = {f["code"] for f in result.detail["features"]}
    assert extra_feature in codes
    assert result.reasons[0]["code"] == extra_feature
    api = settings_to_api(settings, AS_OF, "admin")
    assert extra_feature in {w["code"] for w in api["weights"]}


def test_merge_sanitises_bad_values():
    merged = merge_settings(
        {
            "horizon_days": 9999,
            "thresholds": {"medium": 80, "high": 20},
            "weights": {"prolong_wait": {"weight": 500}, "suspended": "garbage"},
        }
    )
    assert merged.horizon_days == 365
    assert merged.thresholds == {"medium": 35.0, "high": 65.0}
    assert merged.weights["prolong_wait"]["weight"] == 100.0
    assert merged.weights["suspended"] == {"weight": 35.0, "enabled": True}


def test_merge_of_nothing_is_default():
    assert merge_settings(None) == default_settings()


def test_storage_round_trip():
    original = merge_settings(
        {"horizon_days": 14, "weights": {"inactivity": {"weight": 3, "enabled": False}}}
    )
    assert merge_settings(original.to_storage(), version=original.version) == original


def test_settings_to_api_shape():
    api = settings_to_api(default_settings(), datetime(2026, 10, 19, 12, tzinfo=UTC), None)
    assert api["updated_at"] == "2026-10-19T12:00:00Z"
    assert api["updated_by"] is None
    weight = next(w for w in api["weights"] if w["code"] == "autoprolong_off_expiring")
    assert weight == {
        "code": "autoprolong_off_expiring",
        "title": weight["title"],
        "description": weight["description"],
        "group": "renewal",
        "level": "service",
        "weight": 60.0,
        "default_weight": 60.0,
        "min": 0.0,
        "max": 100.0,
        "enabled": True,
    }
