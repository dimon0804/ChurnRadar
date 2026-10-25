"""Монитор дрейфа признаков: PSI текущего распределения против эталона калибровки.

Эталон — распределение сил сигналов по действующим платным услугам синтетической базы, на
которой обучались калибровка и челленджер (model_tables.json, раздел reference). Если
нынешняя база устроена иначе, таблица калибровки и коэффициенты перестают ей
соответствовать, и это видно по PSI:

    PSI = Σ_k (a_k − e_k) · ln(a_k / e_k)

где a_k и e_k — доли услуг в корзине k сейчас и в эталоне. Корзины: «признак молчит» (0)
и четыре интервала силы. Обычное правило: меньше 0,1 — распределение не изменилось,
0,1–0,25 — заметный сдвиг, больше 0,25 — модель пора перепроверить.

На маленькой базе PSI шумит сам по себе, поэтому меньше MIN_SERVICES услуг не считаем.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .calibration import tables
from .scoring import CustomerResult
from .types import Settings

# Верхние границы корзин силы сигнала; первая корзина — ровно 0 (признак не сработал).
BIN_EDGES = (0.0, 0.25, 0.5, 0.75, 1.0)
MIN_SERVICES = 200
EPS = 1e-4
WARN = 0.1
ALERT = 0.25


def bin_of(value: float) -> int:
    if value <= 0:
        return 0
    for i, edge in enumerate(BIN_EDGES[1:], start=1):
        if value <= edge:
            return i
    return len(BIN_EDGES) - 1


def service_values(results: Iterable[CustomerResult]) -> Iterable[dict[str, float]]:
    """Силы сигналов по каждой действующей платной услуге: {код признака: сила}."""
    for result in results:
        for outcome in result.services:
            if outcome.probability is None or outcome.revenue <= 0:
                continue
            yield {hit.feature.code: hit.signal.value for hit in outcome.hits}


def histograms(rows: Iterable[Mapping[str, float]], codes: Sequence[str]) -> tuple[int, dict[str, list[int]]]:
    counts = {code: [0] * len(BIN_EDGES) for code in codes}
    n = 0
    for row in rows:
        n += 1
        for code in codes:
            counts[code][bin_of(row.get(code, 0.0))] += 1
    return n, counts


def psi(actual: Sequence[float], expected: Sequence[float]) -> float:
    """PSI двух распределений по корзинам (доли или счётчики — нормируются здесь)."""
    a_total = sum(actual) or 1.0
    e_total = sum(expected) or 1.0
    value = 0.0
    for a, e in zip(actual, expected, strict=True):
        pa = max(a / a_total, EPS)
        pe = max(e / e_total, EPS)
        value += (pa - pe) * math.log(pa / pe)
    return value


def feature_psi(results: Sequence[CustomerResult], settings: Settings) -> dict[str, Any]:
    """PSI по каждому включённому признаку, у которого есть эталон.

    Возвращает {"services": n, "features": {code: psi}, "status": ...}. Статус no_reference —
    нет таблицы эталона, too_few — услуг меньше MIN_SERVICES (тогда features пустой).
    """
    t = tables()
    reference = None if not t else t.get("reference")
    if not reference:
        return {"services": 0, "features": {}, "status": "no_reference"}
    expected: dict[str, list[float]] = reference["features"]
    codes = [code for code in expected if settings.effective_weight(code) > 0]
    n, counts = histograms(service_values(results), codes)
    if n < MIN_SERVICES:
        return {"services": n, "features": {}, "status": "too_few"}
    values = {code: round(psi(counts[code], expected[code]), 4) for code in codes}
    worst = max(values.values(), default=0.0)
    status = "alert" if worst > ALERT else "warn" if worst > WARN else "ok"
    return {"services": n, "features": values, "status": status}
