from __future__ import annotations

from ..base import Recommendation, ServiceFeature, Signal
from ..context import ServiceContext
from ..registry import register

STRENGTH = {"hour": 1.0, "week": 0.7, "month": 0.3}
WORDING = {"hour": "почасово", "week": "понедельно", "month": "помесячно"}


@register
class ShortCommitment(ServiceFeature):
    code = "short_commitment"
    title = "Короткий период оплаты"
    description = (
        "Чем короче оплаченный период, тем проще уйти: час 1.0, неделя 0.7, месяц 0.3. "
        "Квартал и дольше признак не включают."
    )
    group = "renewal"
    default_weight = 15.0

    def compute(self, ctx: ServiceContext) -> Signal | None:
        term = ctx.service.payment_term
        if term not in STRENGTH:
            return None
        name = ctx.service.name
        return Signal(
            value=STRENGTH[term],
            explanation=f"{name} оплачивается {WORDING[term]}, отказаться можно в любой момент",
            facts={"service": name, "term": WORDING[term]},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title=f"Предложить оплату {signal.facts['service']} на более долгий срок",
            detail="Скидка за переход на квартал или год закрепит клиента.",
        )
