from __future__ import annotations

from ..base import AccountFeature, Recommendation, Signal
from ..context import SECONDS_PER_DAY, AccountContext
from ..money import MONTHLY_FACTOR
from ..registry import register

QUIET_DAYS = 60
RAMP_DAYS = 30
# Сколько молчания сверх оплаченного периода считаем нормальным.
GRACE_DAYS = 30


def quiet_days(ctx: AccountContext) -> float:
    """После скольких дней без платежей молчание становится подозрительным.

    Клиент с одной годовой услугой и не должен платить чаще раза в год, поэтому порог
    отсчитывается от самого короткого периода оплаты среди действующих услуг.
    """
    periods = [30 / MONTHLY_FACTOR[s.payment_term] for s in ctx.live_services]
    shortest = min(periods, default=0.0)
    return max(QUIET_DAYS, shortest + GRACE_DAYS)


@register
class Inactivity(AccountFeature):
    code = "inactivity"
    title = "Давно не платил"
    description = (
        "Успешных платежей нет дольше, чем положено по периоду оплаты: порог 60 дней, а если "
        "самый короткий период у клиента длиннее месяца, то этот период плюс 30 дней. "
        "Сила растёт линейно от 0 на пороге до 1.0 через 30 дней после него."
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
        quiet = quiet_days(ctx)
        if days <= quiet:
            return None
        value = min((days - quiet) / RAMP_DAYS, 1.0)
        return Signal(
            value=value,
            explanation=f"Последний успешный платёж был {int(days)} дн. назад",
            facts={"days": str(int(days))},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        days = signal.facts.get("days")
        silence = f"Платежей не было {days} дн." if days else "Успешных платежей не было"
        return Recommendation(
            code=self.code,
            title=f"{silence}: написать и спросить, всё ли устраивает",
            detail="Предложить помощь с настройкой или подобрать тариф под текущую нагрузку.",
        )
