from __future__ import annotations

from ..base import AccountFeature, Recommendation, Signal
from ..context import SECONDS_PER_DAY, AccountContext
from ..registry import register

QUIET_DAYS = 60
SILENT_DAYS = 90


@register
class Inactivity(AccountFeature):
    code = "inactivity"
    title = "Давно не платил"
    description = (
        "Нет успешных платежей больше 60 дней: сила растёт линейно от 0 на 60-й день до 1.0 на 90-й."
    )
    group = "behavior"
    default_weight = 20.0

    def compute(self, ctx: AccountContext) -> Signal | None:
        if not ctx.transactions:
            return None
        performed = [
            t.performed_at for t in ctx.transactions if t.status == "performed" and t.performed_at is not None
        ]
        if not performed:
            return Signal(1.0, "У клиента нет ни одного успешного платежа")
        days = (ctx.as_of - max(performed)).total_seconds() / SECONDS_PER_DAY
        if days <= QUIET_DAYS:
            return None
        value = min((days - QUIET_DAYS) / (SILENT_DAYS - QUIET_DAYS), 1.0)
        return Signal(
            value=value,
            explanation=f"Последний успешный платёж был {int(days)} дн. назад",
            facts={"days": str(int(days))},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title="Написать клиенту и спросить, всё ли устраивает",
            detail="Платежей давно не было: предложить помощь с настройкой или обзор тарифа.",
        )
