from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from riskcore import Account, Service, Transaction
from riskcore.context import (
    AccountContext,
    ServiceContext,
    build_account_context,
    build_service_context,
)
from riskcore.types import PaymentTerm, ServiceStatus, WebActivity

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_PATH = REPO_ROOT / "data" / "sample" / "data.json"
OPENAPI_PATH = REPO_ROOT / "contracts" / "openapi.yaml"
AS_OF = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)


def make_account(
    id: int = 1,
    balance: int = 100_000,
    withdraw_balance: int = 0,
    bonus_balance: int = 0,
    bonus_state: str = "locked",
    total_replenished: int = 200_000,
) -> Account:
    return Account(
        id=id,
        email=f"user-{id:04d}@example.test",
        balance=balance,
        withdraw_balance=withdraw_balance,
        total_replenished=total_replenished,
        bonus_balance=bonus_balance,
        bonus_state=bonus_state,  # type: ignore[arg-type]
    )


def make_service(
    id: int = 1,
    owner_id: int = 1,
    payment_term: PaymentTerm = "month",
    auto_prolong: bool = True,
    status: ServiceStatus = "active",
    price: int = 1_000,
    expires_in_days: float = 60.0,
    created_days_ago: float = 30.0,
) -> Service:
    return Service(
        id=id,
        name=f"service-{id:04d}",
        owner_id=owner_id,
        payment_term=payment_term,
        auto_prolong=auto_prolong,
        created_at=AS_OF - timedelta(days=created_days_ago),
        expires_at=AS_OF + timedelta(days=expires_in_days),
        status=status,
        price=price,
    )


def make_tx(
    id: int,
    service_id: int = 1,
    status: str = "performed",
    type: str = "prolong",
    days_ago: float = 1.0,
    amount: int = 500,
) -> Transaction:
    created = AS_OF - timedelta(days=days_ago)
    return Transaction(
        id=id,
        service_id=service_id,
        amount=amount,
        status=status,  # type: ignore[arg-type]
        type=type,  # type: ignore[arg-type]
        created_at=created,
        performed_at=created + timedelta(seconds=30) if status == "performed" else None,
    )


def account_ctx(
    account: Account | None = None,
    services: Iterable[Service] = (),
    transactions: Iterable[Transaction] = (),
    horizon_days: int = 30,
    web: WebActivity | None = None,
) -> AccountContext:
    return build_account_context(
        account or make_account(), list(services), list(transactions), AS_OF, horizon_days, web
    )


def make_web(
    account_id: int = 1,
    last_visit_days_ago: float | None = 1.0,
    first_visit_days_ago: float | None = 60.0,
    visits_7d: int = 5,
    visits_30d: int = 20,
    cancel_visits_30d: int = 0,
    last_cancel_days_ago: float | None = None,
) -> WebActivity:
    def ago(days: float | None) -> datetime | None:
        return None if days is None else AS_OF - timedelta(days=days)

    return WebActivity(
        account_id=account_id,
        last_visit_at=ago(last_visit_days_ago),
        first_visit_at=ago(first_visit_days_ago),
        visits_7d=visits_7d,
        visits_30d=visits_30d,
        cancel_visits_30d=cancel_visits_30d,
        last_cancel_visit_at=ago(last_cancel_days_ago),
    )


def service_ctx(
    service: Service,
    account: Account | None = None,
    others: Iterable[Service] = (),
    transactions: Iterable[Transaction] = (),
    horizon_days: int = 30,
) -> ServiceContext:
    ctx = account_ctx(account, [service, *others], transactions, horizon_days)
    return build_service_context(service, ctx)
