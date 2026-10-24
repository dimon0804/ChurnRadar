from __future__ import annotations

from datetime import timedelta

from ..base import AccountFeature, Recommendation, Signal
from ..context import AccountContext
from ..registry import register
from ..text import format_date

WINDOW_DAYS = 30
FIRST_VISIT = 0.6
EACH_NEXT = 0.2


def _times(n: int) -> str:
    return f"{n} раза" if n % 10 in (2, 3, 4) and not 12 <= n % 100 <= 14 else f"{n} раз"


@register
class CancelPageViews(AccountFeature):
    code = "cancel_page_views"
    title = "Смотрел страницу удаления"
    description = (
        "По Яндекс Метрике клиент за последние 30 дней заходил на страницу удаления услуги или "
        "отмены автопродления (адреса и цели задаются в настройках коннектора). Один визит даёт "
        "силу 0.6, каждый следующий +0.2, до 1.0. Если данных Метрики по клиенту нет, признак молчит."
    )
    group = "behavior"
    default_weight = 45.0

    def compute(self, ctx: AccountContext) -> Signal | None:
        web = ctx.web
        if web is None or web.cancel_visits_30d <= 0:
            return None
        last = web.last_cancel_visit_at
        if last is not None and last < ctx.as_of - timedelta(days=WINDOW_DAYS):
            return None
        count = web.cancel_visits_30d
        when = f", последний раз {format_date(last)}" if last is not None else ""
        return Signal(
            value=min(FIRST_VISIT + EACH_NEXT * (count - 1), 1.0),
            explanation=f"За 30 дней {_times(count)} открывал страницу удаления или отмены услуги{when}",
            facts={"count": str(count)},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title="Смотрел, как удалить услугу: позвонить сегодня",
            detail="Спросить, что не устраивает, и предложить решение до того, как клиент удалит сервер.",
        )
