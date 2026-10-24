"""Что делать маркетологу: действие по каждой причине риска и одно главное действие по клиенту."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .base import Feature, Recommendation, Signal
from .money import format_eur, offer_size

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


def action_for(feature: Feature[Any], signal: Signal) -> Recommendation:
    """Действие признака по конкретному срабатыванию; без своего действия — общая формулировка."""
    return feature.recommend(signal) or Recommendation(
        code=feature.code,
        title=f"Разобраться с причиной: {feature.title.lower()}",
        detail=signal.explanation,
    )


def sized_signal(signal: Signal, saving: float) -> Signal:
    """Сигнал с размером промокода под денежный эффект действия.

    Признак кладёт в факты, сколько хотел бы предложить (offer_wanted) и MRR клиента (mrr).
    Здесь размер ограничивается месячной выручкой и пятой частью сбережения, а в факты
    дописываются offer и offer_saving для текста «промокод €X сохранит ожидаемо €Y».
    """
    facts = signal.facts
    if "offer_wanted" not in facts:
        return signal
    size = offer_size(float(facts["offer_wanted"]), float(facts.get("mrr") or 0), saving)
    return replace(
        signal,
        facts={
            **facts,
            "offer": format_eur(size) if size > 0 else "",
            "offer_saving": format_eur(saving) if saving > 0 else "",
        },
    )


def build_recommendations(
    totals: Sequence[FeatureTotal], unknown_code: str | None = None
) -> list[Recommendation]:
    """До трёх действий по убыванию денежного эффекта; первое помечено как главное.

    Денежный эффект — на сколько снизится ожидаемая потеря клиента, если причина исчезнет
    (см. FeatureTotal.saving). Он не совпадает с вкладом в score: вклад делит уже случившийся
    риск между причинами, а эффект отвечает на вопрос «что даст действие».
    """
    if unknown_code is not None:
        action = UNKNOWN_ACTIONS.get(unknown_code)
        return [replace(action, primary=True)] if action else []

    candidates = sorted(
        (total for total in totals if total.points > MIN_POINTS),
        key=lambda t: (-t.saving, -t.points, t.code),
    )
    result: list[Recommendation] = []
    seen: set[str] = set()
    for total in candidates:
        if len(result) >= RECOMMENDATIONS_LIMIT:
            break
        action = replace(
            action_for(total.feature, sized_signal(total.top_hit.signal, total.saving)),
            service_id=total.service_id,
            expected_saving=round(total.saving),
        )
        if action.code not in seen:
            seen.add(action.code)
            result.append(action)
    if result:
        result[0] = replace(result[0], primary=True)
    return result
