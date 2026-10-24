"""Тонкий адаптер к riskcore: каталог признаков и настройки модели.

api не считает риск сам, ему нужны только дефолты, слияние хранимых настроек
с реестром признаков и представление настроек по схеме ModelSettings.
Хранимая форма (scoring.settings.data): {horizon_days, thresholds, weights: {code: {weight, enabled}}}.
"""

from __future__ import annotations

from datetime import datetime
from types import ModuleType
from typing import Any


def _rc() -> ModuleType:
    import riskcore

    return riskcore


def to_api(stored: dict[str, Any] | None, version: int, updated_at: datetime, updated_by: str | None) -> dict[str, Any]:
    rc = _rc()
    api = rc.settings_to_api(rc.merge_settings(stored, version), updated_at, updated_by)
    api["version"] = version
    return api


def defaults_api(updated_at: datetime) -> dict[str, Any]:
    rc = _rc()
    return rc.settings_to_api(rc.default_settings(), updated_at, None)


def storage_from_api(api: dict[str, Any]) -> dict[str, Any]:
    return {
        "horizon_days": int(api["horizon_days"]),
        "thresholds": {"medium": float(api["thresholds"]["medium"]), "high": float(api["thresholds"]["high"])},
        "weights": {
            w["code"]: {"weight": float(w["weight"]), "enabled": bool(w["enabled"])} for w in api["weights"]
        },
    }


def default_storage() -> dict[str, Any]:
    return storage_from_api(defaults_api(datetime.now()))
