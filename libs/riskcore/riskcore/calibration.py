"""Калибровка: score — это ранжирование, вероятность — калиброванная.

Сырая вероятность услуги из noisy-OR хорошо упорядочивает клиентов, но в абсолютных числах
завышена: веса экспертные и не подгонялись под частоту уходов. Поэтому денежные и прогнозные
расчёты (expected_loss, прогноз непродлений, отток в маркетинге) берут калиброванную
вероятность q = f(p), где f — изотоническая регрессия, обученная на синтетическом будущем
(`python -m tools.calibration`). В рантайме это просто таблица точек с линейной интерполяцией:
без sklearn и без numpy.

Здесь же лежат коэффициенты модели-челленджера (логистическая регрессия на тех же признаках)
и эталонное распределение признаков для монитора дрейфа (drift.py). Всё это — один файл
model_tables.json рядом с модулем. Файла нет — калибровка недоступна, модель работает
по сырой вероятности, как раньше.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from .types import Settings

TABLES_FILE = Path(__file__).with_name("model_tables.json")


@lru_cache(maxsize=1)
def tables() -> dict[str, Any] | None:
    try:
        return json.loads(TABLES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _interp(x: float, xs: Sequence[float], ys: Sequence[float]) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect_right(xs, x)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    if x1 == x0:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def available() -> bool:
    t = tables()
    return bool(t and t.get("isotonic"))


def calibrate(p: float, horizon_days: int) -> float | None:
    """Калиброванная вероятность непродления за горизонт или None, если таблицы нет.

    Таблица обучена для своего горизонта (обычно 30 дней). Для другого горизонта считаем,
    что риск за день постоянный: q_H = 1 − (1 − q)^(H / H₀). Это приближение, и оно описано
    в docs/model.md.
    """
    t = tables()
    if not t or not t.get("isotonic"):
        return None
    iso = t["isotonic"]
    q = _interp(p, iso["x"], iso["y"])
    q = min(max(q, 0.0), 1.0)
    base = int(t.get("horizon_days", 30))
    if horizon_days != base and q < 1.0:
        q = 1.0 - (1.0 - q) ** (horizon_days / base)
    return q


def fitted_settings() -> dict[str, Any] | None:
    t = tables()
    return None if not t else t.get("settings")


def matches(settings: Settings) -> bool | None:
    """Совпадают ли веса и группы с теми, на которых обучена таблица.

    Калибровка — отображение сырой вероятности конкретной конфигурации модели. Если
    веса поменяли, таблица по-прежнему монотонна (порядок не портит), но абсолютные числа
    становятся приблизительными — панель об этом предупреждает.
    """
    fitted = fitted_settings()
    if fitted is None:
        return None
    weights = {code: w["weight"] for code, w in settings.weights.items() if w["enabled"]}
    groups = {code: g["cap"] for code, g in settings.groups.items() if g["enabled"]}
    same_weights = all(
        math.isclose(weights.get(code, 0.0), float(value), abs_tol=1e-9)
        for code, value in fitted.get("weights", {}).items()
    ) and set(weights) <= set(fitted.get("weights", {}))
    same_groups = {k: float(v) for k, v in fitted.get("groups", {}).items()} == groups
    return same_weights and same_groups


def challenger_probability(values: Mapping[str, float]) -> float | None:
    """Логистическая регрессия на силах сигналов 0..1; признаки без коэффициента не участвуют."""
    t = tables()
    model = None if not t else t.get("challenger")
    if not model:
        return None
    z = float(model["intercept"])
    for code, coef in model["coef"].items():
        z += float(coef) * values.get(code, 0.0)
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def summary() -> dict[str, Any] | None:
    """Что сейчас загружено: для панели и /api/v1/model/quality."""
    t = tables()
    if not t:
        return None
    return {
        "horizon_days": t.get("horizon_days"),
        "fitted_on": t.get("fitted_on"),
        "points": len(t.get("isotonic", {}).get("x", [])),
        "challenger": None if not t.get("challenger") else t["challenger"].get("kind"),
    }
