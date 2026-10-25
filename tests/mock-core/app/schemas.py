"""Входные модели запросов. Поля исходных данных названы как в data.json."""

from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PaymentTerm = Literal["hour", "week", "month", "quarter_year", "half_year", "year"]
ServiceStatus = Literal["active", "suspended", "prolong_wait", "deleted", "blocked"]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SourceAccount(BaseModel):
    id: int
    email: str
    balance: int = Field(ge=0)
    withdrawBalance: int = Field(ge=0)
    totalReplenished: int = Field(ge=0)
    bonusBalance: int = Field(ge=0)
    bonusState: Literal["locked", "unlocked"]


class SourceService(BaseModel):
    id: int
    name: str
    ownerId: int
    paymentTerm: PaymentTerm
    autoProlong: bool
    createdAt: datetime
    expiresAt: datetime
    status: ServiceStatus
    price: int = Field(ge=0)

    @field_validator("createdAt", "expiresAt")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return _as_utc(value)


class SourceTransaction(BaseModel):
    id: int
    amount: int = Field(ge=0)
    status: Literal["performed", "not_performed"]
    performedAt: Optional[datetime] = None
    createdAt: datetime
    type: Literal["prolong", "order"]
    serviceId: int

    @field_validator("createdAt", "performedAt")
    @classmethod
    def _utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else _as_utc(value)


class SourceSnapshot(BaseModel):
    reference_time: Optional[datetime] = None
    accounts: List[SourceAccount]
    services: List[SourceService]
    transactions: List[SourceTransaction]

    @field_validator("reference_time")
    @classmethod
    def _utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else _as_utc(value)


class SourceChangeBatch(BaseModel):
    accounts: List[SourceAccount] = Field(default_factory=list)
    services: List[SourceService] = Field(default_factory=list)
    transactions: List[SourceTransaction] = Field(default_factory=list)


class ThresholdsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    medium: Optional[float] = Field(default=None, ge=0, le=100)
    high: Optional[float] = Field(default=None, ge=0, le=100)


class WeightPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    weight: Optional[float] = None
    enabled: Optional[bool] = None


class GroupPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    cap: Optional[float] = Field(default=None, ge=0, le=100)
    enabled: Optional[bool] = None


class ModelSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    horizon_days: Optional[int] = Field(default=None, ge=1, le=365)
    thresholds: Optional[ThresholdsPatch] = None
    weights: Optional[List[WeightPatch]] = None
    groups: Optional[List[GroupPatch]] = None
    calibrated: Optional[bool] = None

    @model_validator(mode="after")
    def _not_empty(self) -> "ModelSettingsPatch":
        fields = (self.horizon_days, self.thresholds, self.weights, self.groups, self.calibrated)
        if all(f is None for f in fields):
            raise ValueError("нужно передать хотя бы одно поле")
        return self


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class GeneratePatch(BaseModel):
    accounts: int = Field(default=100, ge=1, le=200000)
    seed: Optional[int] = None


class SimulatorCommand(BaseModel):
    running: Optional[bool] = None
    interval_ms: Optional[int] = Field(default=None, ge=200, le=60000)
    generate: Optional[GeneratePatch] = None
