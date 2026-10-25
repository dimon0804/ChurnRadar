from __future__ import annotations

import math
from datetime import datetime

from ..base import Recommendation, ServiceFeature, Signal
from ..context import ServiceContext
from ..registry import register
from ..text import OVERDUE, format_deadline, format_eur, offer_amount

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
        "которые успеют оплатиться, признак не трогает. Сила равна доле недостающей суммы. "
        "Если выручку услуги ограничивает платёжеспособность клиента, объяснение называет и сумму "
        "продлений по прайсу, и сумму, от которой считается потеря."
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
        date, overdue = format_deadline(funds.shortfall_at, ctx.as_of)
        # продление, которое уже должно было пройти, тоже «наступило»: ждать некогда
        overdue = overdue or ctx.service.expires_at <= ctx.as_of
        bonus = math.ceil(funds.shortfall * TOPUP_BONUS_SHARE / 100) * 100
        return Signal(
            value=value,
            explanation=_explain(ctx, funds.shortfall_at, cost, uncovered),
            facts={
                "service": name,
                "date": date,
                "shortfall": format_eur(funds.shortfall),
                "overdue": "1" if overdue else "",
                "offer_wanted": str(bonus),
                "mrr": str(round(ctx.account_ctx.monthly_revenue)),
                "horizon": str(ctx.horizon_days),
            },
        )

    def recommend(self, signal: Signal) -> Recommendation:
        facts = signal.facts
        if facts.get("overdue"):
            title = f"Нет денег на продление: {OVERDUE}, напомнить пополнить баланс"
        else:
            title = f"Нет денег на продление: напомнить пополнить баланс до {facts['date']}"
        detail = (
            f"Не хватает {facts['shortfall']} на продления за {facts['horizon']} дн., "
            f"иначе {facts['service']} не продлится."
        )
        bonus, saving = offer_amount(facts)
        if bonus:
            detail += f" Предложить бонус {bonus} при пополнении на эту сумму"
            detail += f": бонус {bonus} сохранит ожидаемо {saving}." if saving else "."
        return Recommendation(code=self.code, title=title, detail=detail)


def _explain(ctx: ServiceContext, shortfall_at: datetime, cost: int, uncovered: int) -> str:
    """Коротко: когда кончатся деньги и от какой суммы считаем потерю.

    Дата та же, что «Баланса хватит» в блоке денег: оба считаются по графику автосписаний.
    Если выручку услуги ограничила платёжеспособность клиента, прайсовая сумма продлений
    больше той, от которой считается потеря, и это нужно сказать прямо.
    """
    funds = ctx.funds
    account = ctx.account_ctx
    name = ctx.service.name
    horizon = ctx.horizon_days
    date, _ = format_deadline(shortfall_at, ctx.as_of)
    paid = sum(plan.cost_before(shortfall_at) for plan in account.renewal_plans)
    if paid <= 0:
        text = (
            f"На балансе {account.funds_text}: не хватит уже на продление {date}, "
            f"оплатить {name} будет нечем."
        )
    else:
        text = (
            f"На балансе {account.funds_text}: на автопродления хватит до {date}, "
            f"дальше {name} оплатить будет нечем"
        )
        if uncovered < cost and not ctx.revenue_capped:
            text += f" (без оплаты останутся продления на {format_eur(uncovered)} из {format_eur(cost)})"
        text += "."
    if not ctx.revenue_capped:
        return (
            f"{text} Всего на продления за {horizon} дн. нужно {format_eur(funds.upcoming_renewals_cost)}, "
            f"не хватает {format_eur(funds.shortfall)}."
        )
    if ctx.horizon_revenue > account.payment_capacity:
        basis = "цена одного продления"
    else:
        basis = "столько он может заплатить: баланс плюс обычные пополнения"
    rate = account.replenishment_rate
    pays = f"вносит около {format_eur(rate)} в месяц" if rate >= 100 else "почти не пополняет баланс"
    return (
        f"{text} По прайсу продления {name} за {horizon} дн. стоят {format_eur(cost)}, "
        f"но клиент {pays}, поэтому потерю считаем от {format_eur(ctx.horizon_revenue)} ({basis})."
    )
