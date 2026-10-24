from __future__ import annotations

from ..base import Recommendation, ServiceFeature, Signal
from ..context import ServiceContext
from ..registry import register
from ..text import OVERDUE, format_date, format_in


@register
class AutoprolongOffExpiring(ServiceFeature):
    code = "autoprolong_off_expiring"
    title = "Автопродление выключено"
    description = (
        "Автопродление выключено, а оплаченный срок заканчивается в горизонте прогноза. "
        "Чем ближе дата окончания, тем сильнее сигнал; просроченная услуга даёт 1.0."
    )
    group = "renewal"
    default_weight = 60.0

    def compute(self, ctx: ServiceContext) -> Signal | None:
        service = ctx.service
        if service.auto_prolong or service.status == "prolong_wait":
            return None
        days = ctx.days_to_expiry
        if days > ctx.horizon_days:
            return None
        date = format_date(service.expires_at)
        if days < 0:
            value = 1.0
            explanation = f"Автопродление {service.name} выключено, оплаченный срок закончился {date}"
        else:
            value = 1.0 - min(max(days / ctx.horizon_days, 0.0), 1.0)
            explanation = (
                f"Автопродление {service.name} выключено, услуга заканчивается {format_in(days)} ({date})"
            )
        if value <= 0:
            return None
        ended = "закончилась" if days < 0 else "заканчивается"
        return Signal(value, explanation, {"service": service.name, "date": date, "ended": ended})

    def recommend(self, signal: Signal) -> Recommendation:
        service, date = signal.facts["service"], signal.facts["date"]
        if signal.facts.get("ended") == "закончилась":
            # дату в прошлом в совет «сделать до» не ставим: срок уже наступил
            title = f"Включить автопродление: {OVERDUE}, {service} не оплачена с {date}"
        else:
            title = f"Включить автопродление: напомнить письмом, что {service} заканчивается {date}"
        return Recommendation(
            code=self.code,
            title=title,
            detail="Предложить включить автопродление и дать за это небольшой бонус на баланс.",
        )
