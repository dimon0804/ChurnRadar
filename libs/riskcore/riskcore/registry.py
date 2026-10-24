"""Реестр признаков. Признак попадает сюда через декоратор @register."""

from __future__ import annotations

import importlib
from typing import Any

from .base import AccountFeature, Feature, FeatureSpec, ServiceFeature

AnyFeature = Feature[Any]

REGISTRY: dict[str, AnyFeature] = {}
_loaded = False


def register[F: type[Feature[Any]]](cls: F) -> F:
    feature = cls()
    if not feature.code:
        raise ValueError(f"У признака {cls.__name__} не задан code")
    if feature.code in REGISTRY:
        raise ValueError(f"Признак {feature.code} уже зарегистрирован")
    if not 0 <= feature.default_weight <= 100:
        raise ValueError(f"Вес по умолчанию {feature.code} должен быть в [0, 100]")
    REGISTRY[feature.code] = feature
    return cls


def unregister(code: str) -> None:
    REGISTRY.pop(code, None)


def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        importlib.import_module("riskcore.features")
        _loaded = True


def all_features() -> list[AnyFeature]:
    _ensure_loaded()
    return list(REGISTRY.values())


def get_feature(code: str) -> AnyFeature | None:
    _ensure_loaded()
    return REGISTRY.get(code)


def service_features() -> list[ServiceFeature]:
    return [f for f in all_features() if isinstance(f, ServiceFeature)]


def account_features() -> list[AccountFeature]:
    return [f for f in all_features() if isinstance(f, AccountFeature)]


def feature_catalog() -> list[FeatureSpec]:
    return [feature.spec for feature in all_features()]
