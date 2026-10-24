from __future__ import annotations

from ..base import AccountFeature, Recommendation, Signal
from ..context import SECONDS_PER_DAY, AccountContext
from ..registry import register
from ..text import format_date

# Сколько дней без визитов считаем нормой и за сколько дней после порога сила дорастает до 1.
QUIET_DAYS = 14
RAMP_DAYS = 30


def days_since_visit(ctx: AccountContext) -> float | None:
    """Дней с последнего визита в панель по данным Метрики; None, если данных нет."""
    web = ctx.web
    if web is None or web.last_visit_at is None:
        return None
    return (ctx.as_of - web.last_visit_at).total_seconds() / SECONDS_PER_DAY


@register
class PanelAbsence(AccountFeature):
    code = "panel_absence"
    title = "Давно не заходил в панель"
    description = (
        "По Яндекс Метрике клиент не заходил в панель управления больше 14 дней. Сила растёт "
        "линейно от 0 на 14-й день до 1.0 на 44-й. Визиты связываются с аккаунтом через "
        "setUserID и параметр визита или через таблицу соответствий clientID. Если данных "
        "Метрики по клиенту нет, признак молчит."
    )
    group = "behavior"
    default_weight = 25.0

    def compute(self, ctx: AccountContext) -> Signal | None:
        days = days_since_visit(ctx)
        if days is None or days <= QUIET_DAYS:
            return None
        assert ctx.web is not None and ctx.web.last_visit_at is not None
        value = min((days - QUIET_DAYS) / RAMP_DAYS, 1.0)
        return Signal(
            value=value,
            explanation=(
                f"Не заходил в панель {int(days)} дн.: последний визит "
                f"{format_date(ctx.web.last_visit_at)}"
            ),
            facts={"days": str(int(days))},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title=f"Не заходил в панель {signal.facts['days']} дн.: напомнить о себе",
            detail="Написать, что нового в панели, и спросить, нужна ли помощь с серверами.",
        )
