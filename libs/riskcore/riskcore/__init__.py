"""riskcore: объяснимая модель риска оттока клиентов хостинга."""

from .base import AccountFeature, Feature, FeatureSpec, Recommendation, ServiceFeature, Signal
from .context import AccountContext, Funds, ServiceContext
from .money import monthly_price
from .registry import REGISTRY, feature_catalog, register
from .scoring import CustomerResult, ServiceOutcome, decompose, noisy_or, score_customers
from .settings import default_settings, merge_settings, settings_to_api
from .source import parse_account, parse_service, parse_snapshot, parse_status_change, parse_transaction
from .summary import build_summary, decision_moment
from .types import (
    Account,
    Service,
    Settings,
    StatusChange,
    Thresholds,
    Transaction,
    WeightSetting,
)

__version__ = "1.0.0"

__all__ = [
    "REGISTRY",
    "Account",
    "AccountContext",
    "AccountFeature",
    "CustomerResult",
    "Feature",
    "FeatureSpec",
    "Funds",
    "Recommendation",
    "Service",
    "ServiceContext",
    "ServiceFeature",
    "ServiceOutcome",
    "Settings",
    "Signal",
    "StatusChange",
    "Thresholds",
    "Transaction",
    "WeightSetting",
    "build_summary",
    "decision_moment",
    "decompose",
    "default_settings",
    "feature_catalog",
    "merge_settings",
    "monthly_price",
    "noisy_or",
    "parse_account",
    "parse_service",
    "parse_snapshot",
    "parse_status_change",
    "parse_transaction",
    "register",
    "score_customers",
    "settings_to_api",
]
