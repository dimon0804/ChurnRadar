"""Форматирование чисел и дат для объяснений по-русски."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime

from .money import format_eur, offer_size

__all__ = [
    "OVERDUE",
    "format_date",
    "format_days",
    "format_deadline",
    "format_eur",
    "format_funds",
    "format_in",
    "format_percent",
    "offer_amount",
]

# Как называем срок, который уже прошёл: подставлять в совет дату из прошлого нельзя.
OVERDUE = "срочно, срок уже наступил"


def format_date(moment: datetime) -> str:
    return moment.strftime("%d.%m.%Y")


def format_deadline(moment: datetime, as_of: datetime) -> tuple[str, bool]:
    """Дата для совета «сделать до …»: не раньше опорного момента данных.

    Второй элемент — срок уже прошёл. Тогда дата — сегодняшняя, а в тексте совета нужно
    прямо сказать, что ждать некогда (OVERDUE).
    """
    if moment < as_of:
        return format_date(as_of), True
    return format_date(moment), False


def format_funds(balance: int, bonus: int = 0) -> str:
    """Баланс так же, как в карточке: «€49,47 (вкл. бонусы €6,15)» или просто «€43,32»."""
    if bonus > 0:
        return f"{format_eur(balance + bonus)} (вкл. бонусы {format_eur(bonus)})"
    return format_eur(balance)


def format_days(days: float) -> str:
    return f"{max(0, math.ceil(days))} дн."


def format_in(days: float) -> str:
    """«через ~N дн.» или, для коротких сроков, в часах."""
    hours = days * 24
    if hours < 1:
        return "меньше чем через час"
    if hours < 24:
        return f"через ~{math.ceil(hours)} ч"
    return f"через ~{math.ceil(days)} дн."


def format_percent(share: float) -> str:
    return f"{round(share * 100)}%"


def offer_amount(facts: Mapping[str, str]) -> tuple[str, str]:
    """(промокод, сколько он сохранит) из фактов сигнала, отформатированные; пусто — не предлагать.

    Модель дописывает в факты offer и offer_saving, когда знает денежный эффект действия
    (recommendations.build_recommendations). Без него ограничиваем только месячной выручкой.
    """
    if "offer" in facts:
        return facts["offer"], facts.get("offer_saving", "")
    wanted = float(facts.get("offer_wanted") or 0)
    size = offer_size(wanted, float(facts.get("mrr") or 0))
    return (format_eur(size) if size > 0 else ""), ""
