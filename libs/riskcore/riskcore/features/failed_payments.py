from __future__ import annotations

from ..base import Recommendation, ServiceFeature, Signal
from ..context import ServiceContext
from ..registry import register


@register
class FailedPayments(ServiceFeature):
    code = "failed_payments"
    title = "Платежи не проходят"
    description = "Доля неуспешных платежей за продление услуги. Нужен хотя бы один такой платёж."
    group = "finance"
    default_weight = 30.0

    def compute(self, ctx: ServiceContext) -> Signal | None:
        prolongs = [t for t in ctx.service_transactions if t.type == "prolong"]
        if not prolongs:
            return None
        failed = sum(1 for t in prolongs if t.status == "not_performed")
        if failed == 0:
            return None
        name = ctx.service.name
        return Signal(
            value=failed / len(prolongs),
            explanation=f"По {name} не прошло {failed} из {len(prolongs)} платежей за продление",
            facts={"service": name, "failed": str(failed), "total": str(len(prolongs))},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title="Проверить способ оплаты",
            detail=(
                f"{signal.facts['failed']} из {signal.facts['total']} продлений "
                f"{signal.facts['service']} не прошли: возможно, карта устарела или на ней нет денег."
            ),
        )
