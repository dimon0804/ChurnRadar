from __future__ import annotations

from ..base import Recommendation, ServiceFeature, Signal
from ..context import SECONDS_PER_DAY, ServiceContext
from ..registry import register
from ..text import format_days, format_eur
from ..types import PROLONG_WAIT_GRACE


@register
class ProlongWait(ServiceFeature):
    code = "prolong_wait"
    title = "Ждёт оплаты"
    description = (
        "Услуга в статусе «Ожидает оплаты»: через 5 дней после окончания срока её удалят. "
        "Сила 1.0, если денег на балансе не хватает на продление, иначе 0.6."
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
        when = (
            f"до удаления ~{format_days(days_left)}"
            if days_left > 0
            else "срок ожидания вышел, удалить могут в любой момент"
        )
        money = (
            f"на балансе не хватает {format_eur(shortfall)}"
            if shortfall
            else "деньги на балансе есть, но оплата не прошла"
        )
        return Signal(
            value=1.0 if shortfall else 0.6,
            explanation=f"Услуга {service.name} ждёт оплаты, {when}, {money}",
            facts={
                "service": service.name,
                "days_left": format_days(days_left) if days_left > 0 else "",
                "shortfall": format_eur(shortfall) if shortfall else "",
            },
        )

    def recommend(self, signal: Signal) -> Recommendation:
        shortfall = signal.facts.get("shortfall")
        detail = "Предложить оплатить сейчас или разбить платёж на части."
        if shortfall:
            detail = f"Не хватает {shortfall}. {detail}"
        days_left = signal.facts.get("days_left")
        when = f"через {days_left}" if days_left else "в любой момент"
        return Recommendation(
            code=self.code,
            title=f"Позвонить или написать: {signal.facts['service']} удалят {when}",
            detail=detail,
        )
