"""Контексты, которые готовятся один раз на клиента и передаются признакам."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .money import RenewalPlan, monthly_price, renewal_plan, shortfall_moment
from .types import Account, Service, Transaction

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True, slots=True)
class Funds:
    spendable: int
    upcoming_renewals_cost: int
    shortfall_at: datetime | None

    @property
    def shortfall(self) -> int:
        return max(0, self.upcoming_renewals_cost - self.spendable)


@dataclass(frozen=True, slots=True)
class AccountContext:
    account: Account
    live_services: tuple[Service, ...]
    all_services: tuple[Service, ...]
    transactions: tuple[Transaction, ...]
    as_of: datetime
    horizon_days: int
    renewal_plans: tuple[RenewalPlan, ...]
    funds: Funds

    @property
    def horizon(self) -> int:
        return self.horizon_days

    @property
    def horizon_end(self) -> datetime:
        return self.as_of + timedelta(days=self.horizon_days)

    @property
    def spendable(self) -> int:
        return self.funds.spendable

    @property
    def upcoming_renewals_cost(self) -> int:
        return self.funds.upcoming_renewals_cost

    def plan_for(self, service_id: int) -> RenewalPlan | None:
        return next((plan for plan in self.renewal_plans if plan.service_id == service_id), None)


@dataclass(frozen=True, slots=True)
class ServiceContext:
    service: Service
    account: Account
    as_of: datetime
    horizon_days: int
    days_to_expiry: float
    monthly_price: float
    service_transactions: tuple[Transaction, ...]
    account_ctx: AccountContext
    funds: Funds
    renewal: RenewalPlan | None


def build_account_context(
    account: Account,
    services: Iterable[Service],
    transactions: Iterable[Transaction],
    as_of: datetime,
    horizon_days: int,
) -> AccountContext:
    all_services = tuple(sorted(services, key=lambda s: s.id))
    live = tuple(s for s in all_services if s.is_live)
    plans = tuple(plan for s in live if (plan := renewal_plan(s, as_of, horizon_days)) is not None)
    spendable = account.spendable
    funds = Funds(
        spendable=spendable,
        upcoming_renewals_cost=sum(plan.cost for plan in plans),
        shortfall_at=shortfall_moment(list(plans), spendable),
    )
    return AccountContext(
        account=account,
        live_services=live,
        all_services=all_services,
        transactions=tuple(transactions),
        as_of=as_of,
        horizon_days=horizon_days,
        renewal_plans=plans,
        funds=funds,
    )


def build_service_context(service: Service, account_ctx: AccountContext) -> ServiceContext:
    as_of = account_ctx.as_of
    return ServiceContext(
        service=service,
        account=account_ctx.account,
        as_of=as_of,
        horizon_days=account_ctx.horizon_days,
        days_to_expiry=(service.expires_at - as_of).total_seconds() / SECONDS_PER_DAY,
        monthly_price=monthly_price(service.price, service.payment_term),
        service_transactions=tuple(t for t in account_ctx.transactions if t.service_id == service.id),
        account_ctx=account_ctx,
        funds=account_ctx.funds,
        renewal=account_ctx.plan_for(service.id),
    )
