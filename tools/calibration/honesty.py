"""Честность модели: калибровка score → вероятность, челленджер на ML, группы признаков, эталон дрейфа.

Всё считается на той же синтетической базе и том же будущем, что и основная проверка.

* Калибровка. Изотоническая регрессия сырой вероятности услуги p в факт «услуга потеряна за
  горизонт». Метрики (Brier, reliability) — вне выборки: клиенты делятся на две половины по
  чётности id, таблица обучается на одной и проверяется на другой, и наоборот. В riskcore
  уходит таблица, обученная на всей базе выбранного сценария.
* Челленджер. Логистическая регрессия и градиентный бустинг на тех же силах сигналов
  (scikit-learn, только здесь; в рантайм идут коэффициенты логрега). Те же половины.
* Группы. Модель пересчитывается с группой «Оплата» и разными потолками, сравниваются AUC,
  lift и Brier с моделью без групп.
* Эталон дрейфа. Распределение сил сигналов по корзинам drift.BIN_EDGES.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from . import metrics
from ._deps import ROOT, riskcore, timeutil
from .future import GONE, LIVE

TABLES_PATH = ROOT / "libs" / "riskcore" / "riskcore" / "model_tables.json"
CASE_DATA = ROOT / "data" / "sample" / "data.json"
CASE_AS_OF = "2026-10-19T15:00:00Z"
GROUP_CAPS = (85.0, 70.0, 60.0, 50.0)
RELIABILITY_BINS = 10
TOP_SHARE = 0.10


def _log(message: str) -> None:
    import sys

    print(message, file=sys.stderr, flush=True)


# --- данные -----------------------------------------------------------------------------------------


@dataclass(slots=True)
class ServiceRows:
    """Действующие на T услуги оценённых клиентов, по строке на услугу."""

    customer: np.ndarray
    service: np.ndarray
    p: np.ndarray
    revenue: np.ndarray
    x: np.ndarray  # силы сигналов, столбцы — codes
    codes: list[str]

    def lost(self, final_status: Mapping[int, str]) -> np.ndarray:
        return np.array([final_status.get(int(s)) in GONE for s in self.service], dtype=bool)


def service_rows(results: Sequence[Any], snapshot_services: Sequence[Mapping[str, Any]]) -> ServiceRows:
    live = {int(s["id"]) for s in snapshot_services if s["status"] in LIVE}
    codes = [f.code for f in riskcore.registry.all_features()]
    index = {code: i for i, code in enumerate(codes)}
    cust, svc, p, rev, x = [], [], [], [], []
    for result in results:
        if result.risk_score is None or result.horizon_revenue <= 0:
            continue
        for outcome in result.services:
            if outcome.probability is None or outcome.service.id not in live:
                continue
            row = [0.0] * len(codes)
            for hit in outcome.hits:
                row[index[hit.feature.code]] = hit.signal.value
            cust.append(result.customer_id)
            svc.append(outcome.service.id)
            p.append(outcome.probability)
            rev.append(outcome.revenue)
            x.append(row)
    return ServiceRows(
        np.asarray(cust),
        np.asarray(svc),
        np.asarray(p, dtype=float),
        np.asarray(rev, dtype=float),
        np.asarray(x, dtype=float).reshape(len(p), len(codes)),
        codes,
    )


# --- модели -----------------------------------------------------------------------------------------


def fit_isotonic(p: np.ndarray, y: np.ndarray) -> tuple[list[float], list[float]]:
    from sklearn.isotonic import IsotonicRegression

    iso = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    iso.fit(p, y.astype(float))
    return [float(v) for v in iso.X_thresholds_], [float(v) for v in iso.y_thresholds_]


def apply_isotonic(table: tuple[list[float], list[float]], p: np.ndarray) -> np.ndarray:
    xs, ys = table
    return np.interp(p, xs, ys)


def compress(table: tuple[list[float], list[float]], digits: int = 5) -> dict[str, list[float]]:
    """Таблица для riskcore: округляем и выкидываем точки, лежащие на прямой между соседями."""
    xs = [round(v, digits) for v in table[0]]
    ys = [round(v, digits) for v in table[1]]
    keep_x, keep_y = [xs[0]], [ys[0]]
    for i in range(1, len(xs) - 1):
        if ys[i] == keep_y[-1] and ys[i] == ys[i + 1]:
            continue
        if xs[i] == keep_x[-1] and ys[i] == keep_y[-1]:
            continue
        keep_x.append(xs[i])
        keep_y.append(ys[i])
    if len(xs) > 1:
        keep_x.append(xs[-1])
        keep_y.append(ys[-1])
    return {"x": keep_x, "y": keep_y}


def fit_logreg(x: np.ndarray, y: np.ndarray) -> Any:
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression(C=1.0, max_iter=2000)
    model.fit(x, y)
    return model


def fit_gbm(x: np.ndarray, y: np.ndarray) -> Any:
    from sklearn.ensemble import HistGradientBoostingClassifier

    model = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.08, max_leaf_nodes=15, random_state=0)
    model.fit(x, y)
    return model


def cross_fit(
    fold: np.ndarray, fit: Callable[[np.ndarray], Any], predict: Callable[[Any, np.ndarray], np.ndarray], n: int
) -> np.ndarray:
    """Предсказания вне выборки: обучаем на одной половине, предсказываем другую."""
    out = np.zeros(n)
    for k in (0, 1):
        train, test = fold != k, fold == k
        model = fit(train)
        out[test] = predict(model, test)
    return out


def cross_isotonic(rows: ServiceRows, y: np.ndarray, paid: np.ndarray) -> np.ndarray:
    """Калиброванная вероятность вне выборки: таблица с одной половины клиентов на другую."""
    return cross_fit(
        rows.customer % 2,
        lambda train: fit_isotonic(rows.p[train & paid], y[train & paid]),
        lambda table, test: apply_isotonic(table, rows.p[test]),
        len(rows.p),
    )


# --- метрики ----------------------------------------------------------------------------------------


def lift_at(y: np.ndarray, score: np.ndarray, share: float = TOP_SHARE) -> float:
    base = float(y.mean())
    if base <= 0:
        return math.nan
    rng = np.random.default_rng(0)
    return metrics.precision_at(y, score, share, rng) / base


def reliability(y: np.ndarray, prob: np.ndarray, bins: int = RELIABILITY_BINS) -> list[dict[str, float]]:
    """Reliability-диаграмма: группы по предсказанию (почти равные по числу услуг), среднее и факт."""
    groups = metrics.quantile_bins(prob, bins)
    rows = []
    for g in range(bins):
        m = groups == g
        if not m.any():
            continue
        rows.append({"n": int(m.sum()), "predicted": float(prob[m].mean()), "actual": float(y[m].mean())})
    return rows


def customer_scores(rows: ServiceRows, prob: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Score клиента как в riskcore: средняя вероятность по услугам, взвешенная выручкой."""
    ids, inverse = np.unique(rows.customer, return_inverse=True)
    rev = np.bincount(inverse, weights=rows.revenue)
    loss = np.bincount(inverse, weights=rows.revenue * prob)
    score = np.divide(loss, rev, out=np.zeros_like(loss), where=rev > 0)
    return ids, score


def customer_churned(rows: ServiceRows, lost: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ids, inverse = np.unique(rows.customer, return_inverse=True)
    total = np.bincount(inverse)
    gone = np.bincount(inverse, weights=lost.astype(float))
    return ids, gone >= total


def method_metrics(rows: ServiceRows, lost: np.ndarray, prob: np.ndarray, paid: np.ndarray) -> dict[str, Any]:
    """Метрики одного метода: по услугам (платным) и по клиентам (цель «ушёл полностью»)."""
    y, pr = lost[paid], prob[paid]
    ids, churned = customer_churned(rows, lost)
    _, score = customer_scores(rows, prob)
    paying = np.bincount(np.unique(rows.customer, return_inverse=True)[1], weights=rows.revenue) > 0
    return {
        "service_roc_auc": metrics.roc_auc(y, pr),
        "service_brier": metrics.brier(y, pr),
        "service_lift_top10": lift_at(y, pr),
        "mean_predicted": float(pr.mean()),
        "actual_rate": float(y.mean()),
        "customer_roc_auc": metrics.roc_auc(churned[paying], score[paying]),
        "customer_lift_top10": lift_at(churned[paying], score[paying]),
        "expected_loss": float((rows.revenue * prob).sum()),
        "actual_loss": float(rows.revenue[lost].sum()),
    }


# --- основной расчёт --------------------------------------------------------------------------------


def evaluate_calibration(
    rows: ServiceRows, finals: Mapping[str, Mapping[int, str]], fit_scenario: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Калибровка и челленджеры вне выборки + таблицы, обученные на всей базе fit_scenario."""
    fold = rows.customer % 2
    paid = rows.revenue > 0
    n = len(rows.p)
    lost = {name: rows.lost(final) for name, final in finals.items()}

    # Калибровка, обученная на каждом сценарии и проверенная на каждом: видно, насколько
    # абсолютные числа зависят от допущений симуляции.
    cross: dict[str, dict[str, float]] = {}
    oof_calibrated: dict[str, np.ndarray] = {}
    for fit_name, y_fit in lost.items():
        q = cross_isotonic(rows, y_fit, paid)
        oof_calibrated[fit_name] = q
        cross[fit_name] = {name: metrics.brier(y[paid], q[paid]) for name, y in lost.items()}

    y_fit = lost[fit_scenario]
    logreg = cross_fit(
        fold,
        lambda train: fit_logreg(rows.x[train & paid], y_fit[train & paid]),
        lambda model, test: model.predict_proba(rows.x[test])[:, 1],
        n,
    )
    gbm = cross_fit(
        fold,
        lambda train: fit_gbm(rows.x[train & paid], y_fit[train & paid]),
        lambda model, test: model.predict_proba(rows.x[test])[:, 1],
        n,
    )
    methods = {
        "rules": rows.p,
        "rules_calibrated": oof_calibrated[fit_scenario],
        "logreg": logreg,
        "gbm": gbm,
    }
    by_scenario: dict[str, Any] = {}
    for name, y in lost.items():
        by_scenario[name] = {
            "methods": {method: method_metrics(rows, y, prob, paid) for method, prob in methods.items()},
            "reliability": {
                method: reliability(y[paid], methods[method][paid])
                for method in ("rules", "rules_calibrated", "logreg")
            },
        }

    table = fit_isotonic(rows.p[paid], y_fit[paid])
    final_logreg = fit_logreg(rows.x[paid], y_fit[paid])
    coef = {code: round(float(c), 5) for code, c in zip(rows.codes, final_logreg.coef_[0], strict=True)}
    shipped = {
        "isotonic": compress(table),
        "challenger": {
            "kind": "logreg",
            "intercept": round(float(final_logreg.intercept_[0]), 5),
            "coef": coef,
            "inputs": "силы сигналов признаков 0..1 по услуге",
        },
    }
    report = {
        "fit_scenario": fit_scenario,
        "services": int(paid.sum()),
        "brier_cross": cross,
        "by_scenario": by_scenario,
        "logreg": {"intercept": shipped["challenger"]["intercept"], "coef": coef},
    }
    return report, shipped


def drift_reference(rows: ServiceRows) -> dict[str, Any]:
    paid = rows.revenue > 0
    edges = list(riskcore.drift.BIN_EDGES)
    features: dict[str, list[float]] = {}
    for j, code in enumerate(rows.codes):
        values = rows.x[paid, j]
        counts = Counter(riskcore.drift.bin_of(float(v)) for v in values)
        total = max(int(paid.sum()), 1)
        features[code] = [round(counts.get(k, 0) / total, 5) for k in range(len(edges))]
    return {"bin_edges": edges, "services": int(paid.sum()), "features": features}


def evaluate_groups(
    data: Mapping[str, Any],
    finals: Mapping[str, Mapping[int, str]],
    base_storage: Mapping[str, Any],
    reference: datetime,
    baseline: Sequence[Any],
) -> list[dict[str, Any]]:
    """Модель без групп и с группой «Оплата» под разными потолками: качество и сдвиг уровней."""
    accounts, services, transactions = riskcore.parse_snapshot(data)
    base_levels = {r.customer_id: r.risk_level for r in baseline}
    variants: list[tuple[str, float | None, Sequence[Any]]] = [("без групп", None, baseline)]
    for cap in GROUP_CAPS:
        t0 = time.perf_counter()
        storage = {**base_storage, "groups": {"payment": {"enabled": True, "cap": cap}}}
        settings = riskcore.merge_settings(storage)
        results = riskcore.score_customers(accounts, services, transactions, settings, reference)
        variants.append((f"группа «Оплата», потолок {cap:g}", cap, results))
        _log(f"группы: потолок {cap:g} за {time.perf_counter() - t0:.1f} с")

    out = []
    for label, cap, results in variants:
        rows = service_rows(results, data["services"])
        paid = rows.revenue > 0
        row: dict[str, Any] = {"label": label, "cap": cap, "scenarios": {}}
        changed = sum(1 for r in results if base_levels.get(r.customer_id) != r.risk_level)
        levels = Counter(r.risk_level for r in results)
        row["level_changes"] = changed
        row["levels"] = {level: levels.get(level, 0) for level in ("high", "medium", "low", "unknown")}
        row["expected_loss_raw"] = float((rows.p * rows.revenue).sum())
        for name, final in finals.items():
            y = rows.lost(final)
            q = cross_isotonic(rows, y, paid)
            raw = method_metrics(rows, y, rows.p, paid)
            row["scenarios"][name] = {
                "customer_roc_auc": raw["customer_roc_auc"],
                "customer_lift_top10": raw["customer_lift_top10"],
                "service_roc_auc": raw["service_roc_auc"],
                "service_brier": raw["service_brier"],
                "service_brier_calibrated": metrics.brier(y[paid], q[paid]),
                "high_lift": _high_lift(results, rows, y),
            }
        out.append(row)
    return out


def _high_lift(results: Sequence[Any], rows: ServiceRows, lost: np.ndarray) -> float:
    """Lift уровня high по цели «ушёл полностью» (как в основной таблице уровней)."""
    ids, churned = customer_churned(rows, lost)
    level = {r.customer_id: r.risk_level for r in results}
    high = np.array([level.get(int(i)) == "high" for i in ids])
    base = float(churned.mean())
    if not high.any() or base <= 0:
        return math.nan
    return float(churned[high].mean()) / base


# --- данные кейса -----------------------------------------------------------------------------------


def case_effect(storage_default: Mapping[str, Any]) -> dict[str, Any]:
    """Что меняется на data/sample/data.json: уровни, деньги, прогноз непродлений, отток."""
    data = json.loads(CASE_DATA.read_text(encoding="utf-8"))
    as_of = timeutil.parse_iso(CASE_AS_OF)
    accounts, services, transactions = riskcore.parse_snapshot(data)
    variants = {
        "before": {**storage_default, "calibrated": False, "groups": {"payment": {"enabled": False}}},
        "calibrated": {**storage_default, "calibrated": True, "groups": {"payment": {"enabled": False}}},
        "groups": {**storage_default, "calibrated": False, "groups": {"payment": {"enabled": True}}},
        "default": dict(storage_default),
    }
    out: dict[str, Any] = {"as_of": CASE_AS_OF, "variants": {}}
    base_levels: dict[int, str] = {}
    for name, storage in variants.items():
        settings = riskcore.merge_settings(storage)
        results = riskcore.score_customers(accounts, services, transactions, settings, as_of)
        summary = riskcore.build_summary(results, services, [], settings, as_of)
        marketing = riskcore.build_marketing([r.for_money() for r in results], [], settings.horizon_days, as_of)
        forecast = sum(p["expected_nonrenewals"] or 0.0 for p in summary["timeline"] if p["kind"] == "forecast")
        if name == "before":
            base_levels = {r.customer_id: r.risk_level for r in results}
        out["variants"][name] = {
            "calibrated": settings.calibrated,
            "groups_enabled": settings.groups["payment"]["enabled"],
            "expected_loss": int(summary["expected_loss"]),
            "by_level": summary["by_level"],
            "revenue_at_risk_share": summary["revenue_at_risk_share"],
            "expected_nonrenewals": round(forecast, 2),
            "customer_monthly_churn": marketing["churn"]["customer_monthly"],
            "level_changes": sum(1 for r in results if base_levels.get(r.customer_id) != r.risk_level),
            "customers": [
                {
                    "id": r.customer_id,
                    "score": None if r.risk_score is None else round(r.risk_score, 1),
                    "level": r.risk_level,
                    "expected_loss": r.expected_loss,
                    "calibrated_probability": None
                    if r.calibrated_probability is None
                    else round(r.calibrated_probability, 4),
                    "challenger_score": None if r.challenger_score is None else round(r.challenger_score, 1),
                }
                for r in results
            ],
        }
    psi = riskcore.drift.feature_psi(results, settings)
    out["drift"] = psi
    return out


def tables_digest(path: Path = TABLES_PATH) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12]


def run(
    results: Sequence[Any],
    data: Mapping[str, Any],
    finals: Mapping[str, Mapping[int, str]],
    settings: Any,
    reference: datetime,
    params: Mapping[str, Any],
    fit_scenario: str,
    write_tables: bool,
    groups: bool = True,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    rows = service_rows(results, data["services"])
    report, shipped = evaluate_calibration(rows, finals, fit_scenario)
    _log(f"калибровка и челленджеры за {time.perf_counter() - t0:.1f} с")
    storage = settings.to_storage()
    tables = {
        "version": 1,
        "horizon_days": settings.horizon_days,
        "fitted_on": {
            "scenario": fit_scenario,
            "accounts": params["accounts"],
            "seed": params["seed"],
            "reference": params["reference"],
            "riskcore_sources": params["riskcore_sources"],
            "simulator_sources": params["simulator_sources"],
        },
        "settings": {
            "weights": {code: w["weight"] for code, w in settings.weights.items() if w["enabled"]},
            "groups": {code: g["cap"] for code, g in settings.groups.items() if g["enabled"]},
        },
        **shipped,
        "reference": drift_reference(rows),
    }
    out: dict[str, Any] = {"calibration": report}
    if groups:
        out["groups"] = evaluate_groups(data, finals, storage, reference, results)
    if write_tables:
        payload = json.dumps(tables, ensure_ascii=False, indent=1)
        TABLES_PATH.write_bytes((payload + "\n").encode("utf-8"))
        riskcore.calibration.tables.cache_clear()
        out["case"] = case_effect(_defaults_storage())
    out["tables_sha"] = tables_digest() if write_tables else None
    out["reference_services"] = tables["reference"]["services"]
    out["isotonic_points"] = len(shipped["isotonic"]["x"])
    _log(f"честность модели за {time.perf_counter() - t0:.1f} с")
    return out


def _defaults_storage() -> dict[str, Any]:
    return riskcore.default_settings().to_storage()


# --- Markdown ---------------------------------------------------------------------------------------

METHOD_NAMES = {
    "rules": "правила (сырая p)",
    "rules_calibrated": "правила + изотоническая калибровка",
    "logreg": "логистическая регрессия",
    "gbm": "градиентный бустинг",
}


def _f(value: Any, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    return f"{value:.{digits}f}"


def _eur(cents: float) -> str:
    return f"€{cents / 100:,.0f}".replace(",", " ")


def to_markdown(h: Mapping[str, Any]) -> list[str]:
    cal = h["calibration"]
    out = [
        "## Честность модели",
        "",
        (
            f"Калибровка и челленджеры обучены на сценарии {cal['fit_scenario']}, метрики — вне выборки "
            f"(две половины клиентов по чётности id), {cal['services']} платных услуг."
        ),
        "",
    ]
    for name, sc in cal["by_scenario"].items():
        out += [
            f"Сценарий {name}:",
            "",
            "| Метод | ROC AUC услуг | Brier | Lift топ-10 % услуг | ROC AUC клиентов | Lift топ-10 % клиентов | "
            "Средняя p | Факт | Ожидаемая потеря | Факт потери |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for method, m in sc["methods"].items():
            out.append(
                f"| {METHOD_NAMES[method]} | {_f(m['service_roc_auc'])} | {_f(m['service_brier'], 4)} | "
                f"{_f(m['service_lift_top10'], 2)} | {_f(m['customer_roc_auc'])} | "
                f"{_f(m['customer_lift_top10'], 2)} | {_f(m['mean_predicted'])} | {_f(m['actual_rate'])} | "
                f"{_eur(m['expected_loss'])} | {_eur(m['actual_loss'])} |"
            )
        out.append("")
    names = list(cal["brier_cross"])
    out += [
        "Brier калибровки, обученной на одном сценарии и проверенной на другом:",
        "",
        "| Обучена на \\ проверена на | " + " | ".join(names) + " |",
        "|---" * (len(names) + 1) + "|",
    ]
    for fit, row in cal["brier_cross"].items():
        out.append(f"| {fit} | " + " | ".join(_f(row[n], 4) for n in names) + " |")
    out.append("")
    if h.get("groups"):
        out += [
            "Группа «Оплата» (prolong_wait, insufficient_funds, failed_payments):",
            "",
            "| Вариант | Сменили уровень | high | stream AUC клиентов | stream lift high | "
            "calendar AUC клиентов | calendar lift high | calendar Brier калибр. |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for g in h["groups"]:
            s, c = g["scenarios"].get("stream", {}), g["scenarios"].get("calendar", {})
            out.append(
                f"| {g['label']} | {g['level_changes']} | {g['levels']['high']} | "
                f"{_f(s.get('customer_roc_auc'))} | {_f(s.get('high_lift'), 2)} | "
                f"{_f(c.get('customer_roc_auc'))} | {_f(c.get('high_lift'), 2)} | "
                f"{_f(c.get('service_brier_calibrated'), 4)} |"
            )
        out.append("")
    if h.get("case"):
        out += [
            f"Данные кейса (data/sample/data.json, {h['case']['as_of']}):",
            "",
            "| Вариант | Ожидаемая потеря | Уровни high/medium/low/unknown | Сменили уровень | "
            "Прогноз непродлений | Отток клиентов в месяц |",
            "|---|---|---|---|---|---|",
        ]
        for name, v in h["case"]["variants"].items():
            lv = v["by_level"]
            out.append(
                f"| {name} | €{v['expected_loss'] / 100:,.2f} | "
                f"{lv['high']}/{lv['medium']}/{lv['low']}/{lv['unknown']} | {v['level_changes']} | "
                f"{_f(v['expected_nonrenewals'], 2)} | {_f(v['customer_monthly_churn'])} |"
            )
        out.append("")
    return out
