from __future__ import annotations

from ..base import Recommendation, ServiceFeature, Signal
from ..context import ServiceContext
from ..registry import register
from ..text import format_eur


@register
class Suspended(ServiceFeature):
    code = "suspended"
    title = "Сервер выключен"
    description = "Клиент сам выключил сервер: деньги списываются, а сервисом он не пользуется."
    group = "behavior"
    default_weight = 35.0

    def compute(self, ctx: ServiceContext) -> Signal | None:
        if ctx.service.status != "suspended":
            return None
        name = ctx.service.name
        return Signal(
            value=1.0,
            explanation=f"Сервер {name} выключен клиентом: платит, но не пользуется",
            facts={"service": name, "monthly": format_eur(ctx.horizon_revenue * 30 / ctx.horizon_days)},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title=f"Сервер {signal.facts['service']} выключен: написать и узнать причину",
            detail=(
                f"Клиент платит около {signal.facts.get('monthly', '')} в месяц за сервер, "
                "которым не пользуется: предложить тариф дешевле или паузу вместо отказа."
            ),
        )
