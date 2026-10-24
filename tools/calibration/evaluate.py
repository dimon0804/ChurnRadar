"""Сведение оценки модели на момент T с фактическим исходом через горизонт и расчёт метрик."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from . import metrics
from ._deps import riskcore, timeutil
from .future import GONE, LIVE

LEVELS = ("low", "medium", "high")
SEGMENT_ORDER = {"loyal": 0, "newcomer": 1, "risky": 2, "churning": 3}
TOP_SHARES = (0.05, 0.10)
ROC_GRID = np.linspace(0.0, 1.0, 101)
# методы, для которых сохраняем саму ROC-кривую; случайный порядок — диагональ, её рисует панель
ROC_METHODS = ("model", "autoprolong_off", "prolong_wait", "segment_oracle")


@dataclass(slots=True)
class Accounts:
    """Оценённые клиенты (уровень не unknown), по одному элементу массива на клиента."""

    ids: np.ndarray
    score: np.ndarray
    level: np.ndarray
    expected_loss: np.ndarray
    revenue: np.ndarray
    lost_revenue: np.ndarray
    churned: np.ndarray
    lost_any: np.ndarray
    autoprolong_off_share: np.ndarray
    waiting_share: np.ndarray
    has_waiting: np.ndarray
    segment: np.ndarray

    def subset(self, mask: np.ndarray) -> Accounts:
        return Accounts(**{name: getattr(self, name)[mask] for name in self.__slots__})


@dataclass(slots=True)
class Services:
    probability: np.ndarray
    lost: np.ndarray
    paid_beyond: np.ndarray  # оплаченный срок на T заканчивается позже T + горизонт


def _service_view(result: Any, horizon_days: int) -> list[tuple[int, float, float]]:
    """(id, выручка за горизонт, вероятность непродления) по действующим услугам клиента.

    Выручка берётся та же, что модель кладёт в horizon_revenue услуги: выручка в горизонте
    с ограничением по платёжеспособности. Так факт и expected_loss считаются от одной базы.
    """
    rows = []
    for outcome in getattr(result, "services", ()):
        if outcome.probability is None:
            continue
        rows.append((outcome.service.id, float(outcome.revenue), float(outcome.probability)))
    if rows:
        return rows
    # Запасной путь, если в CustomerResult нет разбивки по услугам.
    for item in result.detail.get("services", []):
        if item.get("risk_score") is None:
            continue
        revenue = item.get("horizon_revenue")
        if revenue is None:
            revenue = riskcore.monthly_price(item["price"], item["payment_term"]) * horizon_days / 30
        rows.append((int(item["id"]), float(revenue), float(item["risk_score"]) / 100))
    return rows


def build_tables(
    results: Iterable[Any],
    snapshot_services: Iterable[Mapping[str, Any]],
    final_status: Mapping[int, str],
    segments: Mapping[int, str],
    horizon_days: int,
    reference: datetime,
) -> tuple[Accounts, Services]:
    at_start = {int(s["id"]): s for s in snapshot_services}
    horizon_end = reference + timedelta(days=horizon_days)
    acc: dict[str, list[Any]] = {name: [] for name in Accounts.__slots__}
    svc_p: list[float] = []
    svc_lost: list[bool] = []
    svc_beyond: list[bool] = []
    for result in results:
        if result.risk_score is None or result.risk_level not in LEVELS:
            continue
        rows = [r for r in _service_view(result, horizon_days) if at_start[r[0]]["status"] in LIVE]
        revenue = sum(r[1] for r in rows)
        if revenue <= 0:
            continue
        lost = [final_status.get(sid) in GONE for sid, _, _ in rows]
        lost_revenue = sum(r[1] for r, gone in zip(rows, lost, strict=True) if gone)
        off = sum(r[1] for r in rows if not at_start[r[0]]["autoProlong"])
        waiting = sum(r[1] for r in rows if at_start[r[0]]["status"] == "prolong_wait")
        acc["ids"].append(result.customer_id)
        acc["score"].append(float(result.risk_score))
        acc["level"].append(result.risk_level)
        acc["expected_loss"].append(sum(r[1] * r[2] for r in rows))
        acc["revenue"].append(revenue)
        acc["lost_revenue"].append(lost_revenue)
        acc["churned"].append(all(lost))
        acc["lost_any"].append(any(lost))
        acc["autoprolong_off_share"].append(off / revenue)
        acc["waiting_share"].append(waiting / revenue)
        acc["has_waiting"].append(waiting > 0)
        acc["segment"].append(segments.get(result.customer_id, "newcomer"))
        for (sid, rev, p), gone in zip(rows, lost, strict=True):
            if rev > 0:
                svc_p.append(p)
                svc_lost.append(gone)
                svc_beyond.append(timeutil.parse_iso(at_start[sid]["expiresAt"]) > horizon_end)
    arrays = {name: np.asarray(values) for name, values in acc.items()}
    services = Services(
        np.asarray(svc_p, dtype=float), np.asarray(svc_lost, dtype=bool), np.asarray(svc_beyond, dtype=bool)
    )
    return Accounts(**arrays), services


def _scores(table: Accounts, rng: np.random.Generator) -> dict[str, np.ndarray]:
    return {
        "model": table.score,
        "autoprolong_off": table.autoprolong_off_share,
        "prolong_wait": table.waiting_share,
        "random": rng.random(len(table.score)),
        "segment_oracle": np.array([SEGMENT_ORDER[s] for s in table.segment], dtype=float),
    }


def ranking(table: Accounts, target: str, rng: np.random.Generator, bootstrap: int) -> dict[str, dict[str, Any]]:
    y = getattr(table, target).astype(bool)
    out: dict[str, dict[str, Any]] = {}
    for name, score in _scores(table, rng).items():
        row: dict[str, Any] = {
            "roc_auc": metrics.roc_auc(y, score),
            "pr_auc": metrics.average_precision(y, score),
        }
        for share in TOP_SHARES:
            row[f"precision_top{round(share * 100)}"] = metrics.precision_at(y, score, share, rng)
            row[f"recall_top{round(share * 100)}"] = metrics.recall_at(y, score, share, rng)
        if name == "model":
            row["roc_auc_ci"] = metrics.bootstrap_ci(metrics.roc_auc, y, score, bootstrap, rng)
            row["pr_auc_ci"] = metrics.bootstrap_ci(metrics.average_precision, y, score, bootstrap, rng)
        out[name] = row
    return out


def roc_points(table: Accounts, target: str) -> dict[str, Any]:
    """ROC-кривые на общей сетке FPR (101 точка): компактно для JSON и панели."""
    y = getattr(table, target).astype(bool)
    scores = {
        "model": table.score,
        "autoprolong_off": table.autoprolong_off_share,
        "prolong_wait": table.waiting_share,
        "segment_oracle": np.array([SEGMENT_ORDER[s] for s in table.segment], dtype=float),
    }
    return {
        "target": target,
        "fpr": [round(float(v), 4) for v in ROC_GRID],
        "tpr": {
            name: [round(float(v), 4) for v in metrics.roc_on_grid(y, scores[name], ROC_GRID)]
            for name in ROC_METHODS
        },
    }


def threshold_metrics(table: Accounts) -> dict[str, dict[str, Any]]:
    """Точность и полнота, если звонить всем с уровнем high (или medium и выше)."""
    out: dict[str, dict[str, Any]] = {}
    for name, flagged_levels in (("high", ("high",)), ("medium_plus", ("medium", "high"))):
        flagged = np.isin(table.level, flagged_levels)
        row: dict[str, Any] = {"flagged": int(flagged.sum()), "flagged_share": float(flagged.mean())}
        for target in ("churned", "lost_any"):
            y = getattr(table, target).astype(bool)
            hit = int((y & flagged).sum())
            row[target] = {
                "precision": hit / int(flagged.sum()) if flagged.any() else math.nan,
                "recall": hit / int(y.sum()) if y.any() else math.nan,
            }
        out[name] = row
    return out


def by_level(table: Accounts) -> list[dict[str, Any]]:
    base_churn = float(table.churned.mean())
    base_any = float(table.lost_any.mean())
    rows = []
    for level in (*LEVELS, "all"):
        m = np.ones(len(table.level), dtype=bool) if level == "all" else table.level == level
        n = int(m.sum())
        if n == 0:
            rows.append({"level": level, "n": 0})
            continue
        revenue = float(table.revenue[m].sum())
        churn = float(table.churned[m].mean())
        lost_any = float(table.lost_any[m].mean())
        rows.append(
            {
                "level": level,
                "n": n,
                "share_of_base": n / len(table.level),
                "churn_rate": churn,
                "churn_lift": churn / base_churn if base_churn else math.nan,
                "lost_any_rate": lost_any,
                "lost_any_lift": lost_any / base_any if base_any else math.nan,
                "revenue": revenue,
                "expected_loss": float(table.expected_loss[m].sum()),
                "actual_loss": float(table.lost_revenue[m].sum()),
                "predicted_share": float(table.expected_loss[m].sum() / revenue),
                "actual_share": float(table.lost_revenue[m].sum() / revenue),
            }
        )
    return rows


def evaluate(table: Accounts, services: Services, rng: np.random.Generator, bootstrap: int) -> dict[str, Any]:
    hard = table.subset(~table.has_waiting)
    return {
        "accounts": len(table.ids),
        "services": len(services.lost),
        "base_rates": {
            "churned": float(table.churned.mean()),
            "lost_any": float(table.lost_any.mean()),
            "revenue_lost_share": float(table.lost_revenue.sum() / table.revenue.sum()),
        },
        "ranking": {
            "churned": ranking(table, "churned", rng, bootstrap),
            "lost_any": ranking(table, "lost_any", rng, bootstrap),
        },
        "levels": by_level(table),
        "calibration": metrics.calibration_table(table.score, table.expected_loss, table.revenue, table.lost_revenue),
        "money": {
            "expected_loss": float(table.expected_loss.sum()),
            "actual_loss": float(table.lost_revenue.sum()),
            "revenue": float(table.revenue.sum()),
        },
        "service_level": {
            "roc_auc": metrics.roc_auc(services.lost, services.probability),
            "pr_auc": metrics.average_precision(services.lost, services.probability),
            "brier": metrics.brier(services.lost, services.probability),
            "mean_probability": float(services.probability.mean()),
            "lost_rate": float(services.lost.mean()),
            # Доля потерянных услуг, у которых на T срок был оплачен дальше горизонта:
            # в настоящем биллинге такая услуга за горизонт удалиться не может.
            "lost_paid_beyond_horizon": float(services.paid_beyond[services.lost].mean())
            if services.lost.any()
            else math.nan,
        },
        "hard_subset": {
            "description": "клиенты без услуг в статусе prolong_wait на момент T",
            "accounts": len(hard.ids),
            "base_rates": {
                "churned": float(hard.churned.mean()) if len(hard.ids) else math.nan,
                "lost_any": float(hard.lost_any.mean()) if len(hard.ids) else math.nan,
            },
            "ranking": {
                "churned": ranking(hard, "churned", rng, 0),
                "lost_any": ranking(hard, "lost_any", rng, 0),
            },
            "levels": by_level(hard),
        },
        "roc": roc_points(table, "churned"),
        "thresholds": threshold_metrics(table),
    }
