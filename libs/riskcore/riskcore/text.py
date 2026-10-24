"""Форматирование чисел и дат для объяснений по-русски."""

from __future__ import annotations

import math
from datetime import datetime

from .money import format_eur

__all__ = ["format_date", "format_days", "format_eur", "format_in", "format_percent"]


def format_date(moment: datetime) -> str:
    return moment.strftime("%d.%m.%Y")


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
