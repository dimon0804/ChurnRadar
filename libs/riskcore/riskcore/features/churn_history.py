from __future__ import annotations

from ..base import AccountFeature, Recommendation, Signal
from ..context import AccountContext
from ..registry import register
from ..types import GONE_STATUSES


@register
class ChurnHistory(AccountFeature):
    code = "churn_history"
    title = "Уже отказывался от услуг"
    description = "Доля удалённых и заблокированных услуг среди всех услуг клиента (нужно хотя бы две)."
    group = "behavior"
    default_weight = 40.0

    def compute(self, ctx: AccountContext) -> Signal | None:
        total = len(ctx.all_services)
        if total < 2:
            return None
        gone = sum(1 for s in ctx.all_services if s.status in GONE_STATUSES)
        if gone == 0:
            return None
        return Signal(
            value=gone / total,
            explanation=f"Клиент уже лишился {gone} из {total} услуг: они удалены или заблокированы",
            facts={"gone": str(gone), "total": str(total)},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title=(
                f"Уже ушло {signal.facts['gone']} из {signal.facts['total']} услуг: "
                "подключить персонального менеджера"
            ),
            detail="Подготовить удерживающее предложение, пока клиент не закрыл оставшиеся услуги.",
        )
