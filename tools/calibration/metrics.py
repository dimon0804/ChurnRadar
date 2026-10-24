"""Метрики качества ранжирования и калибровки на numpy, без scikit-learn."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np


def _rank_average(values: np.ndarray) -> np.ndarray:
    """Ранги с 1, одинаковые значения получают средний ранг."""
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    upper = np.cumsum(counts)
    average = upper - (counts - 1) / 2
    return average[inverse].astype(float)


def roc_auc(y: np.ndarray, score: np.ndarray) -> float:
    """Площадь под ROC через статистику Манна — Уитни (ничьи считаются за половину)."""
    y = np.asarray(y, dtype=bool)
    score = np.asarray(score, dtype=float)
    pos = int(y.sum())
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return math.nan
    ranks = _rank_average(score)
    return float((ranks[y].sum() - pos * (pos + 1) / 2) / (pos * neg))


def roc_curve(y: np.ndarray, score: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Точки ROC по уникальным порогам, от (0, 0) до (1, 1)."""
    y = np.asarray(y, dtype=bool)
    score = np.asarray(score, dtype=float)
    order = np.argsort(-score, kind="mergesort")
    s, t = score[order], y[order]
    last = np.r_[np.flatnonzero(np.diff(s)), len(s) - 1]
    tp = np.cumsum(t)[last]
    fp = (last + 1) - tp
    pos, neg = max(int(y.sum()), 1), max(len(y) - int(y.sum()), 1)
    return np.r_[0.0, fp / neg], np.r_[0.0, tp / pos]


def roc_on_grid(y: np.ndarray, score: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """TPR ROC-кривой в заданных точках FPR.

    Между порогами кривая линейна (как при случайном разбиении ничьих). На вертикальном
    участке берётся верхняя точка: последний порог с FPR ≤ g, дальше прямая к следующему.
    """
    fpr, tpr = roc_curve(y, score)
    grid = np.asarray(grid, dtype=float)
    i = np.clip(np.searchsorted(fpr, grid, side="right") - 1, 0, len(fpr) - 1)
    j = np.minimum(i + 1, len(fpr) - 1)
    span = fpr[j] - fpr[i]
    weight = np.divide(grid - fpr[i], span, out=np.zeros_like(grid), where=span > 0)
    return tpr[i] + weight * (tpr[j] - tpr[i])


def average_precision(y: np.ndarray, score: np.ndarray) -> float:
    """PR AUC как average precision: Σ (R_i − R_{i−1}) · P_i по уникальным порогам."""
    y = np.asarray(y, dtype=bool)
    score = np.asarray(score, dtype=float)
    pos = int(y.sum())
    if pos == 0:
        return math.nan
    order = np.argsort(-score, kind="mergesort")
    s, t = score[order], y[order]
    last = np.r_[np.flatnonzero(np.diff(s)), len(s) - 1]
    tp = np.cumsum(t)[last]
    precision = tp / (last + 1)
    recall = tp / pos
    return float(np.sum(np.diff(np.r_[0.0, recall]) * precision))


def top_k_mask(score: np.ndarray, share: float, rng: np.random.Generator) -> np.ndarray:
    """Маска первых ceil(share·n) по убыванию score; ничьи разбиваются случайно."""
    score = np.asarray(score, dtype=float)
    k = max(1, math.ceil(share * len(score)))
    order = np.lexsort((rng.random(len(score)), -score))
    mask = np.zeros(len(score), dtype=bool)
    mask[order[:k]] = True
    return mask


def precision_at(y: np.ndarray, score: np.ndarray, share: float, rng: np.random.Generator) -> float:
    return float(np.asarray(y, dtype=bool)[top_k_mask(score, share, rng)].mean())


def recall_at(y: np.ndarray, score: np.ndarray, share: float, rng: np.random.Generator) -> float:
    y = np.asarray(y, dtype=bool)
    return float(y[top_k_mask(score, share, rng)].sum() / max(int(y.sum()), 1))


def bootstrap_ci(
    metric: Callable[[np.ndarray, np.ndarray], float],
    y: np.ndarray,
    score: np.ndarray,
    rounds: int,
    rng: np.random.Generator,
    level: float = 0.95,
) -> tuple[float, float]:
    """Перцентильный доверительный интервал по бутстрепу клиентов."""
    if rounds <= 0:
        return (math.nan, math.nan)
    n = len(y)
    values = []
    for _ in range(rounds):
        idx = rng.integers(0, n, n)
        value = metric(y[idx], score[idx])
        if not math.isnan(value):
            values.append(value)
    if not values:
        return (math.nan, math.nan)
    tail = (1 - level) / 2 * 100
    low, high = np.percentile(values, [tail, 100 - tail])
    return float(low), float(high)


def quantile_bins(score: np.ndarray, bins: int) -> np.ndarray:
    """Номер группы 0..bins−1 по рангу score: группы почти равного размера, ничьи в одной группе."""
    score = np.asarray(score, dtype=float)
    ranks = _rank_average(score)
    return np.minimum(((ranks - 1) * bins / len(score)).astype(int), bins - 1)


def calibration_table(
    score: np.ndarray,
    predicted_loss: np.ndarray,
    revenue: np.ndarray,
    actual_loss: np.ndarray,
    bins: int = 10,
) -> list[dict[str, float]]:
    """По группам score: предсказанная и фактическая доля потерянной выручки (взвешено выручкой)."""
    groups = quantile_bins(score, bins)
    rows = []
    for g in range(bins):
        m = groups == g
        if not m.any():
            continue
        rev = float(revenue[m].sum())
        rows.append(
            {
                "group": g + 1,
                "n": int(m.sum()),
                "score_min": float(score[m].min()),
                "score_max": float(score[m].max()),
                "predicted_share": float(predicted_loss[m].sum() / rev) if rev else math.nan,
                "actual_share": float(actual_loss[m].sum() / rev) if rev else math.nan,
            }
        )
    return rows


def brier(y: np.ndarray, probability: np.ndarray) -> float:
    return float(np.mean((np.asarray(probability, dtype=float) - np.asarray(y, dtype=float)) ** 2))
