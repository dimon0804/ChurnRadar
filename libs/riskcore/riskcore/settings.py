from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .registry import all_features
from .types import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_THRESHOLDS,
    Settings,
    Thresholds,
    WeightSetting,
    isoformat_z,
)

WEIGHT_MIN = 0.0
WEIGHT_MAX = 100.0
HORIZON_MIN = 1
HORIZON_MAX = 365


def _clip(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def default_settings() -> Settings:
    return Settings(
        version=1,
        horizon_days=DEFAULT_HORIZON_DAYS,
        thresholds=Thresholds(**DEFAULT_THRESHOLDS),
        weights={f.code: WeightSetting(weight=float(f.default_weight), enabled=True) for f in all_features()},
    )


def merge_settings(stored: Mapping[str, Any] | None, version: int | None = None) -> Settings:
    """Хранимые настройки поверх реестра.

    Признак, которого нет в хранимых данных, получает вес по умолчанию и включается:
    так новый файл в features/ сразу попадает в модель без миграций. Коды, которых уже
    нет в реестре, отбрасываются.
    """
    stored = stored or {}
    base = default_settings()

    horizon = int(_clip(int(stored.get("horizon_days", base.horizon_days)), HORIZON_MIN, HORIZON_MAX))

    raw_thresholds = stored.get("thresholds") or {}
    thresholds = Thresholds(
        medium=_clip(float(raw_thresholds.get("medium", base.thresholds["medium"])), 0, 100),
        high=_clip(float(raw_thresholds.get("high", base.thresholds["high"])), 0, 100),
    )
    if thresholds["medium"] > thresholds["high"]:
        thresholds = Thresholds(**base.thresholds)

    raw_weights: Mapping[str, Any] = stored.get("weights") or {}
    weights: dict[str, WeightSetting] = {}
    for code, default in base.weights.items():
        entry = raw_weights.get(code)
        if not isinstance(entry, Mapping):
            weights[code] = WeightSetting(**default)
            continue
        weights[code] = WeightSetting(
            weight=_clip(float(entry.get("weight", default["weight"])), WEIGHT_MIN, WEIGHT_MAX),
            enabled=bool(entry.get("enabled", default["enabled"])),
        )

    resolved_version = version if version is not None else int(stored.get("version", base.version))
    return Settings(
        version=resolved_version,
        horizon_days=horizon,
        thresholds=thresholds,
        weights=weights,
    )


def settings_to_api(s: Settings, updated_at: datetime, updated_by: str | None) -> dict[str, Any]:
    """Настройки в форме схемы ModelSettings."""
    weights = []
    for feature in all_features():
        entry = s.weights.get(feature.code) or WeightSetting(
            weight=float(feature.default_weight), enabled=True
        )
        weights.append(
            {
                "code": feature.code,
                "title": feature.title,
                "description": feature.description,
                "group": feature.group,
                "level": feature.level,
                "weight": float(entry["weight"]),
                "default_weight": float(feature.default_weight),
                "min": WEIGHT_MIN,
                "max": WEIGHT_MAX,
                "enabled": bool(entry["enabled"]),
            }
        )
    return {
        "version": s.version,
        "updated_at": isoformat_z(updated_at),
        "updated_by": updated_by,
        "horizon_days": s.horizon_days,
        "thresholds": {"medium": float(s.thresholds["medium"]), "high": float(s.thresholds["high"])},
        "weights": weights,
    }
