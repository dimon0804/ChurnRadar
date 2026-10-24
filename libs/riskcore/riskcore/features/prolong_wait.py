from __future__ import annotations

from ..base import Recommendation, ServiceFeature, Signal
from ..context import SECONDS_PER_DAY, ServiceContext
from ..registry import register
from ..text import format_days, format_eur
from ..types import PROLONG_WAIT_GRACE

BASE_STRENGTH = 0.6


@register
class ProlongWait(ServiceFeature):
    code = "prolong_wait"
    title = "Ждёт оплаты"
    description = (
        "Услуга в статусе «Ожидает оплаты»: через 5 дней после окончания срока её удалят. "
        "Сила 0.6. Если автопродление выключено и на балансе не хватает на оплату, сила 1.0. "
        "При включённом автопродлении нехватку денег учитывает признак «Не хватит денег на продление», "
        "чтобы один и тот же пустой баланс не считался дважды."
    )
    group = "renewal"
    default_weight = 85.0

    def compute(self, ctx: ServiceContext) -> Signal | None:
        service = ctx.service
        if service.status != "prolong_wait":
            return None
        deletion_at = service.expires_at + PROLONG_WAIT_GRACE
        days_left = (deletion_at - ctx.as_of).total_seconds() / SECONDS_PER_DAY
        shortfall = max(0, service.price - ctx.funds.spendable)
        # С автопродлением деньги на эту услугу уже входят в график продлений клиента,
        # и их нехватку оценивает insufficient_funds.
        money_counted_here = ctx.renewal is None
        when = (
            f"до удаления ~{format_days(days_left)}"
            if days_left > 0
            else "срок ожидания вышел, удалить могут в любой момент"
        )
        if shortfall:
            money = f"на балансе не хватает {format_eur(shortfall)}"
        elif service.auto_prolong:
            money = "деньги на балансе есть, но автосписание не прошло"
        else:
            money = "деньги на балансе есть, автопродление выключено"
        value = 1.0 if shortfall and money_counted_here else BASE_STRENGTH
        return Signal(
            value=value,
            explanation=f"Услуга {service.name} ждёт оплаты, {when}, {money}",
            facts={
                "service": service.name,
                "days_left": format_days(days_left) if days_left > 0 else "",
                "shortfall": format_eur(shortfall) if shortfall else "",
                "spendable": format_eur(ctx.funds.spendable),
            },
        )

    def recommend(self, signal: Signal) -> Recommendation:
        days_left = signal.facts.get("days_left")
        when = f"удалят через {days_left}" if days_left else "могут удалить в любой момент"
        shortfall = signal.facts.get("shortfall")
        if shortfall:
            detail = f"Не хватает {shortfall}: предложить промокод на эту сумму или оплату частями."
        else:
            detail = (
                f"На балансе {signal.facts.get('spendable', '')}, но оплата не прошла: "
                "помочь оплатить вручную или проверить карту."
            )
        return Recommendation(
            code=self.code,
            title=f"Срочно написать: {signal.facts['service']} {when}",
            detail=detail,
        )
