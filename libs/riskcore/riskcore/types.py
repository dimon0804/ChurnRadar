from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict

PaymentTerm = Literal["hour", "week", "month", "quarter_year", "half_year", "year"]
ServiceStatus = Literal["active", "suspended", "prolong_wait", "deleted", "blocked"]
BonusState = Literal["locked", "unlocked"]
TransactionStatus = Literal["performed", "not_performed"]
TransactionType = Literal["prolong", "order"]
RiskLevel = Literal["high", "medium", "low", "unknown"]
FeatureLevel = Literal["service", "account"]

LIVE_STATUSES: frozenset[str] = frozenset({"active", "suspended", "prolong_wait"})
GONE_STATUSES: frozenset[str] = frozenset({"deleted", "blocked"})
PROLONG_WAIT_GRACE = timedelta(days=5)


def ensure_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def isoformat_z(moment: datetime) -> str:
    return ensure_utc(moment).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    email: str
    balance: int
    withdraw_balance: int
    total_replenished: int
    bonus_balance: int
    bonus_state: BonusState

    @property
    def spendable(self) -> int:
        """Деньги, которыми можно оплатить продление: баланс и разблокированные бонусы."""
        return self.balance + (self.bonus_balance if self.bonus_state == "unlocked" else 0)


@dataclass(frozen=True, slots=True)
class Service:
    id: int
    name: str
    owner_id: int
    payment_term: PaymentTerm
    auto_prolong: bool
    created_at: datetime
    expires_at: datetime
    status: ServiceStatus
    price: int

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_STATUSES


@dataclass(frozen=True, slots=True)
class Transaction:
    id: int
    service_id: int
    amount: int
    status: TransactionStatus
    type: TransactionType
    created_at: datetime
    performed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StatusChange:
    service_id: int
    owner_id: int
    old_status: ServiceStatus | None
    new_status: ServiceStatus
    changed_at: datetime
    estimated: bool = False


class Thresholds(TypedDict):
    medium: float
    high: float


class WeightSetting(TypedDict):
    weight: float
    enabled: bool


DEFAULT_HORIZON_DAYS = 30
DEFAULT_THRESHOLDS: Thresholds = {"medium": 35.0, "high": 65.0}


@dataclass(frozen=True, slots=True)
class Settings:
    version: int = 1
    horizon_days: int = DEFAULT_HORIZON_DAYS
    thresholds: Thresholds = field(default_factory=lambda: Thresholds(**DEFAULT_THRESHOLDS))
    weights: dict[str, WeightSetting] = field(default_factory=dict)

    def effective_weight(self, code: str) -> float:
        """Действующий коэффициент 0..1; выключенный или неизвестный признак даёт 0."""
        entry = self.weights.get(code)
        if entry is None or not entry["enabled"]:
            return 0.0
        return entry["weight"] / 100.0

    def to_storage(self) -> dict[str, object]:
        """Содержимое колонки scoring.settings.data (версия хранится отдельно)."""
        return {
            "horizon_days": self.horizon_days,
            "thresholds": dict(self.thresholds),
            "weights": {code: dict(entry) for code, entry in self.weights.items()},
        }

    def level_for(self, score: float | None) -> RiskLevel:
        if score is None:
            return "unknown"
        if score >= self.thresholds["high"]:
            return "high"
        if score >= self.thresholds["medium"]:
            return "medium"
        return "low"
