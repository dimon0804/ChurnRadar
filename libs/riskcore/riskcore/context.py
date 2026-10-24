"""Контексты, которые готовятся один раз на клиента и передаются признакам."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .money import (
    RenewalPlan,
    collectible_revenue,
    funds_run_out,
    horizon_revenue,
    monthly_price,
    payment_capacity,
    renewal_plan,
    replenishment_rate,
    shortfall_moment,
)
from .types import Account, Service, Transaction, WebActivity

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True, slots=True)
class Funds:
    """Деньги клиента против графика автопродлений.

    shortfall_at — первое продление внутри горизонта, на которое денег уже нет. runs_out_at —
    тот же момент, но с поиском дальше горизонта (до года): из него считается, на сколько
    дней хватит баланса. Внутри горизонта оба момента совпадают.
    """

    spendable: int
    upcoming_renewals_cost: int
    shortfall_at: datetime | None
    runs_out_at: datetime | None = None
    has_renewals: bool = False

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
    replenishment_rate: float = 0.0
    payment_capacity: float = float("inf")
    # визиты на сайт из Яндекс Метрики; None — данных по клиенту нет, признаки сайта молчат
    web: WebActivity | None = None

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
    contract_revenue: float = 0.0
    horizon_revenue: float = 0.0

    @property
    def revenue_capped(self) -> bool:
        return self.horizon_revenue < self.contract_revenue

    def uncovered_cost(self) -> int:
        """Сколько продлений этой услуги в горизонте придётся на момент, когда деньги уже кончились."""
        if self.renewal is None or self.funds.shortfall_at is None:
            return 0
        return self.renewal.cost - self.renewal.cost_before(self.funds.shortfall_at)


def _lifetime_days(
    services: Iterable[Service], transactions: Iterable[Transaction], as_of: datetime
) -> float:
    """Сколько дней клиент с нами: от самой ранней услуги или транзакции."""
    moments = [s.created_at for s in services] + [t.created_at for t in transactions]
    if not moments:
        return 0.0
    return max((as_of - min(moments)).total_seconds() / SECONDS_PER_DAY, 0.0)


def build_account_context(
    account: Account,
    services: Iterable[Service],
    transactions: Iterable[Transaction],
    as_of: datetime,
    horizon_days: int,
    web: WebActivity | None = None,
) -> AccountContext:
    all_services = tuple(sorted(services, key=lambda s: s.id))
    transactions = tuple(transactions)
    live = tuple(s for s in all_services if s.is_live)
    plans = tuple(plan for s in live if (plan := renewal_plan(s, as_of, horizon_days)) is not None)
    spendable = account.spendable
    rate = replenishment_rate(account.total_replenished, _lifetime_days(all_services, transactions, as_of))
    shortfall_at = shortfall_moment(list(plans), spendable)
    funds = Funds(
        spendable=spendable,
        upcoming_renewals_cost=sum(plan.cost for plan in plans),
        shortfall_at=shortfall_at,
        runs_out_at=funds_run_out(live, as_of, spendable, horizon_days, shortfall_at),
        has_renewals=any(s.auto_prolong and s.price > 0 for s in live),
    )
    return AccountContext(
        account=account,
        live_services=live,
        all_services=all_services,
        transactions=transactions,
        as_of=as_of,
        horizon_days=horizon_days,
        renewal_plans=plans,
        funds=funds,
        replenishment_rate=rate,
        payment_capacity=payment_capacity(spendable, rate, horizon_days),
        web=web,
    )


def build_service_context(service: Service, account_ctx: AccountContext) -> ServiceContext:
    as_of = account_ctx.as_of
    monthly = monthly_price(service.price, service.payment_term)
    contract = horizon_revenue(monthly, account_ctx.horizon_days)
    return ServiceContext(
        service=service,
        account=account_ctx.account,
        as_of=as_of,
        horizon_days=account_ctx.horizon_days,
        days_to_expiry=(service.expires_at - as_of).total_seconds() / SECONDS_PER_DAY,
        monthly_price=monthly,
        service_transactions=tuple(t for t in account_ctx.transactions if t.service_id == service.id),
        account_ctx=account_ctx,
        funds=account_ctx.funds,
        renewal=account_ctx.plan_for(service.id),
        contract_revenue=contract,
        horizon_revenue=collectible_revenue(contract, account_ctx.payment_capacity, service.price),
    )
