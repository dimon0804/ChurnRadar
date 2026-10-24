from __future__ import annotations

from datetime import timedelta

from ..base import AccountFeature, Recommendation, Signal
from ..context import AccountContext
from ..registry import register
from .panel_absence import QUIET_DAYS, days_since_visit

# Обычная неделя считается по 23 дням до последней недели (30 − 7).
EARLIER_DAYS = 23
# Чтобы было с чем сравнивать: визиты видны хотя бы четыре недели и обычно их от двух в неделю.
MIN_HISTORY_DAYS = 28
MIN_WEEKLY = 2.0
# Падение ниже половины обычного уровня; при нуле визитов за неделю сила 1.0.
DROP_RATIO = 0.5


def _visits(n: float) -> str:
    n = round(n)
    tail = n % 100
    if 11 <= tail <= 14:
        word = "визитов"
    elif n % 10 == 1:
        word = "визит"
    elif n % 10 in (2, 3, 4):
        word = "визита"
    else:
        word = "визитов"
    return f"{n} {word}"


@register
class WebActivityDrop(AccountFeature):
    code = "web_activity_drop"
    title = "Реже заходит на сайт"
    description = (
        "По Яндекс Метрике визитов за последние 7 дней меньше половины обычной недели "
        "(среднее за предыдущие 23 дня). Нужна история хотя бы за 4 недели и от двух визитов "
        "в неделю. Сила растёт от 0 при половине обычного до 1.0 при нуле. Если клиент не "
        "заходил больше 14 дней, срабатывает «Давно не заходил в панель», а этот признак молчит, "
        "чтобы не считать одно и то же дважды."
    )
    group = "behavior"
    default_weight = 20.0

    def compute(self, ctx: AccountContext) -> Signal | None:
        web = ctx.web
        if web is None or web.first_visit_at is None:
            return None
        if web.first_visit_at > ctx.as_of - timedelta(days=MIN_HISTORY_DAYS):
            return None
        days = days_since_visit(ctx)
        if days is not None and days > QUIET_DAYS:
            return None
        weekly = max(web.visits_30d - web.visits_7d, 0) * 7 / EARLIER_DAYS
        if weekly < MIN_WEEKLY:
            return None
        ratio = web.visits_7d / weekly
        if ratio >= DROP_RATIO:
            return None
        return Signal(
            value=min(1.0 - ratio / DROP_RATIO, 1.0),
            explanation=(
                f"За неделю {_visits(web.visits_7d)} на сайт, а обычно около {_visits(weekly)} в неделю"
            ),
            facts={"week": str(web.visits_7d), "usual": str(round(weekly))},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title=(
                f"Заходит реже обычного ({signal.facts['week']} против ~{signal.facts['usual']} в неделю): "
                "узнать, что изменилось"
            ),
            detail="Спросить, не переезжает ли проект и хватает ли ресурсов. Предложить подходящий тариф.",
        )
