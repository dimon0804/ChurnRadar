"""Схемы тела запросов ingest (SourceSnapshot, SourceChangeBatch из contracts/openapi.yaml).

Записи источника — pydantic-датаклассы со slots: снимок на 100k аккаунтов — это больше
миллиона объектов, и без __dict__ и служебных полей BaseModel он занимает в разы меньше памяти.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.dataclasses import dataclass

PaymentTerm = Literal["hour", "week", "month", "quarter_year", "half_year", "year"]
ServiceStatus = Literal["active", "suspended", "prolong_wait", "deleted", "blocked"]

BIGINT_MAX = 9_223_372_036_854_775_807
Id = Annotated[int, Field(ge=1, le=BIGINT_MAX)]
Money = Annotated[int, Field(ge=0, le=BIGINT_MAX)]

_CONFIG = ConfigDict(extra="ignore", populate_by_name=True)


@dataclass(slots=True, kw_only=True, config=_CONFIG)
class SourceAccount:
    id: Id
    email: str = Field(min_length=1, max_length=320)
    balance: Money
    withdraw_balance: Money = Field(alias="withdrawBalance")
    total_replenished: Money = Field(alias="totalReplenished")
    bonus_balance: Money = Field(alias="bonusBalance")
    bonus_state: Literal["locked", "unlocked"] = Field(alias="bonusState")


@dataclass(slots=True, kw_only=True, config=_CONFIG)
class SourceService:
    id: Id
    name: str = Field(min_length=1, max_length=500)
    owner_id: Id = Field(alias="ownerId")
    payment_term: PaymentTerm = Field(alias="paymentTerm")
    auto_prolong: bool = Field(alias="autoProlong")
    created_at: AwareDatetime = Field(alias="createdAt")
    expires_at: AwareDatetime = Field(alias="expiresAt")
    status: ServiceStatus
    price: Money


@dataclass(slots=True, kw_only=True, config=_CONFIG)
class SourceTransaction:
    id: Id
    amount: Money
    status: Literal["performed", "not_performed"]
    performed_at: AwareDatetime | None = Field(default=None, alias="performedAt")
    created_at: AwareDatetime = Field(alias="createdAt")
    type: Literal["prolong", "order"]
    service_id: Id = Field(alias="serviceId")


@dataclass(slots=True, kw_only=True, config=_CONFIG)
class SourceSnapshot:
    reference_time: AwareDatetime | None = None
    accounts: list[SourceAccount]
    services: list[SourceService]
    transactions: list[SourceTransaction]


@dataclass(slots=True, kw_only=True, config=_CONFIG)
class SourceChangeBatch:
    accounts: list[SourceAccount] = Field(default_factory=list)
    services: list[SourceService] = Field(default_factory=list)
    transactions: list[SourceTransaction] = Field(default_factory=list)


SNAPSHOT = TypeAdapter(SourceSnapshot)
SERVICE = TypeAdapter(SourceService)


class Counts(BaseModel):
    accounts: int
    services: int
    transactions: int


class SnapshotAccepted(BaseModel):
    snapshot_id: str
    counts: Counts


class EventsAccepted(BaseModel):
    accepted: int


def _dupes(values: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    dup: list[Any] = []
    for v in values:
        if v in seen and v not in dup:
            dup.append(v)
        seen.add(v)
    return dup


def check_batch_consistency(
    accounts: list[SourceAccount],
    services: list[SourceService],
    transactions: list[SourceTransaction],
    known_account_ids: set[int] | None = None,
    known_service_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Проверки, которые не выражаются схемой: уникальность и ссылочная целостность.

    known_* — идентификаторы, уже существующие в БД (для потока изменений).
    Возвращает список ошибок в формате details; пустой список — всё в порядке.
    """
    errors: list[dict[str, Any]] = []

    def add(field: str, message: str, value: Any, kind: str) -> None:
        errors.append({"field": field, "message": message, "value": value, "type": kind})

    for v in _dupes([a.id for a in accounts]):
        add("accounts.id", f"Аккаунт {v} встречается несколько раз", v, "duplicate")
    for v in _dupes([a.email.strip().lower() for a in accounts]):
        add("accounts.email", f"Email {v} встречается у нескольких аккаунтов", v, "duplicate")
    for v in _dupes([s.id for s in services]):
        add("services.id", f"Услуга {v} встречается несколько раз", v, "duplicate")
    for v in _dupes([t.id for t in transactions]):
        add("transactions.id", f"Транзакция {v} встречается несколько раз", v, "duplicate")

    account_ids = {a.id for a in accounts} | (known_account_ids or set())
    service_ids = {s.id for s in services} | (known_service_ids or set())

    for i, s in enumerate(services):
        if s.owner_id not in account_ids:
            add(f"services.{i}.ownerId", f"Услуга {s.id} ссылается на несуществующий аккаунт {s.owner_id}", s.owner_id, "broken_reference")
        if s.expires_at < s.created_at:
            add(f"services.{i}.expiresAt", f"У услуги {s.id} дата окончания раньше даты создания", s.expires_at, "invalid_range")
    for i, t in enumerate(transactions):
        if t.service_id not in service_ids:
            add(f"transactions.{i}.serviceId", f"Транзакция {t.id} ссылается на несуществующую услугу {t.service_id}", t.service_id, "broken_reference")
    return errors

