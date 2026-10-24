"""Что делать маркетологу: действие по каждой из главных причин риска."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .base import Recommendation

if TYPE_CHECKING:
    from .scoring import FeatureTotal

RECOMMENDATIONS_LIMIT = 3
MIN_POINTS = 0.5

UNKNOWN_ACTIONS: dict[str, Recommendation] = {
    "all_services_gone": Recommendation(
        code="win_back",
        title="Предложить вернуться",
        detail="Все услуги клиента уже закрыты: подойдёт скидка на первый месяц при новом заказе.",
    ),
    "no_live_services": Recommendation(
        code="first_order",
        title="Помочь с первым заказом",
        detail="Клиент зарегистрирован, но ничего не купил: предложить подобрать тариф.",
    ),
}


def build_recommendations(
    totals: Sequence[FeatureTotal], unknown_code: str | None = None
) -> list[Recommendation]:
    if unknown_code is not None:
        action = UNKNOWN_ACTIONS.get(unknown_code)
        return [action] if action else []

    result: list[Recommendation] = []
    seen: set[str] = set()
    for total in totals:
        if total.points <= MIN_POINTS or len(result) >= RECOMMENDATIONS_LIMIT:
            break
        action = total.feature.recommend(total.top_hit.signal) or Recommendation(
            code=total.code,
            title=f"Разобраться с причиной: {total.feature.title.lower()}",
            detail=total.top_hit.signal.explanation,
        )
        if action.code not in seen:
            seen.add(action.code)
            result.append(action)
    return result
