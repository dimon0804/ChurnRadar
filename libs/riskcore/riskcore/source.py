"""Разбор исходных записей: принимает и DTO кейса (camelCase), и строки БД (snake_case)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, cast

from .types import (
    Account,
    BonusState,
    PaymentTerm,
    Service,
    ServiceStatus,
    StatusChange,
    Transaction,
    TransactionStatus,
    TransactionType,
    ensure_utc,
)


def _get(row: Mapping[str, Any], snake: str, camel: str | None = None) -> Any:
    if snake in row:
        return row[snake]
    if camel is not None and camel in row:
        return row[camel]
    raise KeyError(snake)


def _moment(value: Any) -> datetime:
    if isinstance(value, datetime):
        return ensure_utc(value)
    return ensure_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _optional_moment(value: Any) -> datetime | None:
    return None if value is None else _moment(value)


def parse_account(row: Mapping[str, Any]) -> Account:
    return Account(
        id=int(row["id"]),
        email=str(row["email"]),
        balance=int(row["balance"]),
        withdraw_balance=int(_get(row, "withdraw_balance", "withdrawBalance")),
        total_replenished=int(_get(row, "total_replenished", "totalReplenished")),
        bonus_balance=int(_get(row, "bonus_balance", "bonusBalance")),
        bonus_state=cast(BonusState, _get(row, "bonus_state", "bonusState")),
    )


def parse_service(row: Mapping[str, Any]) -> Service:
    return Service(
        id=int(row["id"]),
        name=str(row["name"]),
        owner_id=int(_get(row, "owner_id", "ownerId")),
        payment_term=cast(PaymentTerm, _get(row, "payment_term", "paymentTerm")),
        auto_prolong=bool(_get(row, "auto_prolong", "autoProlong")),
        created_at=_moment(_get(row, "created_at", "createdAt")),
        expires_at=_moment(_get(row, "expires_at", "expiresAt")),
        status=cast(ServiceStatus, row["status"]),
        price=int(row["price"]),
    )


def parse_transaction(row: Mapping[str, Any]) -> Transaction:
    performed = row.get("performed_at", row.get("performedAt"))
    return Transaction(
        id=int(row["id"]),
        service_id=int(_get(row, "service_id", "serviceId")),
        amount=int(row["amount"]),
        status=cast(TransactionStatus, row["status"]),
        type=cast(TransactionType, row["type"]),
        created_at=_moment(_get(row, "created_at", "createdAt")),
        performed_at=_optional_moment(performed),
    )


def parse_status_change(row: Mapping[str, Any]) -> StatusChange:
    old_status = row.get("old_status")
    return StatusChange(
        service_id=int(row["service_id"]),
        owner_id=int(row["owner_id"]),
        old_status=cast(ServiceStatus | None, old_status),
        new_status=cast(ServiceStatus, row["new_status"]),
        changed_at=_moment(row["changed_at"]),
        estimated=bool(row.get("estimated", False)),
    )


def parse_snapshot(
    data: Mapping[str, Iterable[Mapping[str, Any]]],
) -> tuple[list[Account], list[Service], list[Transaction]]:
    return (
        [parse_account(row) for row in data.get("accounts", ())],
        [parse_service(row) for row in data.get("services", ())],
        [parse_transaction(row) for row in data.get("transactions", ())],
    )
