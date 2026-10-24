from __future__ import annotations

from ..base import Recommendation, ServiceFeature, Signal
from ..context import ServiceContext
from ..registry import register
from ..text import format_date, format_eur


@register
class InsufficientFunds(ServiceFeature):
    code = "insufficient_funds"
    title = "Не хватит денег на продление"
    description = (
        "Автопродление включено, но баланса и разблокированных бонусов не хватит на все "
        "продления клиента в горизонте. Сила равна доле суммы, которой не хватает."
    )
    group = "finance"
    default_weight = 55.0

    def compute(self, ctx: ServiceContext) -> Signal | None:
        if not ctx.service.auto_prolong or ctx.renewal is None:
            return None
        funds = ctx.funds
        if funds.upcoming_renewals_cost <= 0 or funds.shortfall <= 0:
            return None
        value = min(max(funds.shortfall / funds.upcoming_renewals_cost, 0.0), 1.0)
        date = format_date(funds.shortfall_at or ctx.as_of)
        return Signal(
            value=value,
            explanation=(
                f"На продления за {ctx.horizon_days} дн. нужно {format_eur(funds.upcoming_renewals_cost)}, "
                f"а на балансе {format_eur(funds.spendable)}: не хватит {format_eur(funds.shortfall)}, "
                f"деньги закончатся {date}"
            ),
            facts={
                "service": ctx.service.name,
                "date": date,
                "shortfall": format_eur(funds.shortfall),
                "horizon": str(ctx.horizon_days),
            },
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title=f"Напомнить пополнить баланс до {signal.facts['date']}",
            detail=(
                f"Нужно ещё {signal.facts['shortfall']}, чтобы продления "
                f"за {signal.facts['horizon']} дн. прошли без сбоев."
            ),
        )
