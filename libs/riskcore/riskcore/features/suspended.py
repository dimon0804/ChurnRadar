from __future__ import annotations

from ..base import Recommendation, ServiceFeature, Signal
from ..context import ServiceContext
from ..registry import register


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
            facts={"service": name},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title=f"Узнать, почему выключен сервер {signal.facts['service']}",
            detail="Предложить тариф дешевле или паузу вместо отказа.",
        )
