from __future__ import annotations

import math

from ..base import Recommendation, ServiceFeature, Signal
from ..context import ServiceContext
from ..registry import register
from ..text import format_date, format_eur

# Бонус к пополнению, который предлагаем, чтобы клиент закрыл нехватку.
TOPUP_BONUS_SHARE = 0.10


@register
class InsufficientFunds(ServiceFeature):
    code = "insufficient_funds"
    title = "Не хватит денег на продление"
    description = (
        "Автопродление включено, но баланса и разблокированных бонусов не хватит на продления "
        "клиента в горизонте. Продления оплачиваются по очереди, поэтому сигнал получают только "
        "те услуги, чьи продления придутся на момент, когда деньги уже кончились; продления, "
        "которые успеют оплатиться, признак не трогает. Сила равна доле недостающей суммы."
    )
    group = "finance"
    default_weight = 55.0

    def compute(self, ctx: ServiceContext) -> Signal | None:
        if not ctx.service.auto_prolong or ctx.renewal is None:
            return None
        funds = ctx.funds
        if funds.upcoming_renewals_cost <= 0 or funds.shortfall <= 0 or funds.shortfall_at is None:
            return None
        cost = ctx.renewal.cost
        uncovered = ctx.uncovered_cost()
        if cost <= 0 or uncovered <= 0:
            return None
        # Сила — насколько велика дыра в деньгах клиента: нехватку в пару евро закрывают
        # обычным пополнением, а пустой баланс при крупных продлениях почти наверняка означает отказ.
        value = min(max(funds.shortfall / funds.upcoming_renewals_cost, 0.0), 1.0)
        name = ctx.service.name
        date = format_date(funds.shortfall_at)
        partial = (
            f" (без оплаты останутся продления на {format_eur(uncovered)} из {format_eur(cost)})"
            if uncovered < cost
            else ""
        )
        bonus = math.ceil(funds.shortfall * TOPUP_BONUS_SHARE / 100) * 100
        return Signal(
            value=value,
            explanation=(
                f"На продления за {ctx.horizon_days} дн. нужно {format_eur(funds.upcoming_renewals_cost)}, "
                f"а на балансе {format_eur(funds.spendable)}: не хватит {format_eur(funds.shortfall)}, "
                f"деньги закончатся {date}, "
                f"и оплатить {name} будет нечем{partial}"
            ),
            facts={
                "service": name,
                "date": date,
                "shortfall": format_eur(funds.shortfall),
                "bonus": format_eur(bonus),
                "horizon": str(ctx.horizon_days),
            },
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title=f"Нет денег на продление: напомнить пополнить баланс до {signal.facts['date']}",
            detail=(
                f"Не хватает {signal.facts['shortfall']} на продления за {signal.facts['horizon']} дн. "
                f"Предложить бонус {signal.facts['bonus']} при пополнении на эту сумму, "
                f"иначе {signal.facts['service']} не продлится."
            ),
        )
