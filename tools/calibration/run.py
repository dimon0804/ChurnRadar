"""CLI: python -m tools.calibration [--accounts 20000 --seed 42 ...]

Генерирует базу, считает риск моделью на момент T, прогоняет будущее на горизонт
в двух сценариях и сохраняет метрики (JSON и таблицы Markdown) и графики.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import evaluate, future
from ._deps import ROOT, riskcore, timeutil

SCENARIOS = ("stream", "calendar")
DEFAULT_REFERENCE = "2026-10-01T12:00:00Z"
NAMES = {
    "model": "модель (score)",
    "autoprolong_off": "доля выручки без автопродления",
    "prolong_wait": "доля выручки в «ждёт оплаты»",
    "random": "случайный порядок",
    "segment_oracle": "скрытый сегмент (недоступен модели)",
}
LEVEL_NAMES = {"low": "низкий", "medium": "средний", "high": "высокий", "all": "все"}
# Прогоны на устойчивость: что меняем относительно основного и какие сценарии при этом считаем.
ROBUSTNESS = (
    ("seed 43", {"seed": 43}, SCENARIOS),
    ("seed 44", {"seed": 44}, SCENARIOS),
    ("темп потока 1/12 в сутки", {"stream_rate": 1 / 12}, ("stream",)),
    ("темп потока 1/3 в сутки", {"stream_rate": 1 / 3}, ("stream",)),
    ("разброс поведения сильнее (concentration 2)", {"concentration": 2.0}, ("calendar",)),
)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Проверка качества модели риска на синтетическом будущем")
    p.add_argument("--accounts", type=int, default=20_000, help="размер базы (по умолчанию 20000)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--reference", default=DEFAULT_REFERENCE, help="момент T, ISO 8601")
    p.add_argument("--horizon", type=int, default=30, help="горизонт в днях")
    p.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=list(SCENARIOS))
    p.add_argument(
        "--stream-rate",
        type=float,
        default=1 / 6,
        help="изменений живого потока на одну действующую услугу в сутки (stream)",
    )
    p.add_argument(
        "--background-rate",
        type=float,
        default=0.02,
        help="фоновых изменений на одну действующую услугу в сутки (calendar)",
    )
    p.add_argument(
        "--concentration",
        type=float,
        default=8.0,
        help="разброс поведения внутри сегмента, параметр Beta; 0 — без разброса (calendar)",
    )
    p.add_argument("--bootstrap", type=int, default=200, help="число бутстреп-выборок для интервалов")
    p.add_argument("--out", type=Path, default=ROOT / "tools" / "calibration" / "results")
    p.add_argument("--img", type=Path, default=ROOT / "docs" / "img" / "calibration")
    p.add_argument("--no-plots", action="store_true")
    p.add_argument(
        "--no-robustness",
        action="store_true",
        help="не считать дополнительные прогоны с другими seed и параметрами (быстрее в 3 раза)",
    )
    args = p.parse_args(argv)
    if not 100 <= args.accounts <= 200_000:
        p.error("--accounts должен быть от 100 до 200000")
    if not 1 <= args.horizon <= 365:
        p.error("--horizon должен быть от 1 до 365")
    return args


def json_safe(value: Any) -> Any:
    """NaN и бесконечности в null: так results.json остаётся валидным JSON для любого читателя."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    return value


def fingerprint(directory: Path) -> str:
    """Короткий хеш исходников: по нему видно, на какой версии кода посчитаны цифры."""
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*.py")):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:12]


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def run(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, evaluate.Accounts]]:
    started = time.perf_counter()
    reference = timeutil.parse_iso(args.reference)
    data, segments = future.generate_with_segments(args.accounts, args.seed, reference)
    _log(f"база: {len(data['accounts'])} клиентов, {len(data['services'])} услуг")

    settings = riskcore.merge_settings({"horizon_days": args.horizon})
    accounts, services, transactions = riskcore.parse_snapshot(data)
    results = riskcore.score_customers(accounts, services, transactions, settings, reference)
    _log(f"модель посчитана за {time.perf_counter() - started:.1f} с")

    report: dict[str, Any] = {
        "params": {
            "accounts": args.accounts,
            "seed": args.seed,
            "reference": args.reference,
            "horizon_days": args.horizon,
            "stream_rate": args.stream_rate,
            "background_rate": args.background_rate,
            "concentration": args.concentration,
            "bootstrap": args.bootstrap,
            "riskcore_version": getattr(riskcore, "__version__", "?"),
            "riskcore_sources": fingerprint(ROOT / "libs" / "riskcore" / "riskcore"),
            "simulator_sources": fingerprint(ROOT / "services" / "simulator" / "app"),
            "weights": {code: w["weight"] for code, w in settings.weights.items() if w["enabled"]},
            "thresholds": dict(settings.thresholds),
        },
        "scenarios": {},
    }
    tables: dict[str, evaluate.Accounts] = {}
    for offset, scenario in enumerate(SCENARIOS):
        if scenario not in args.scenarios:
            continue
        t0 = time.perf_counter()
        sim_seed = args.seed * 1000 + offset + 1
        if scenario == "stream":
            outcome = future.simulate_stream(
                data, seed=sim_seed, horizon_days=args.horizon, changes_per_service_day=args.stream_rate
            )
        else:
            outcome = future.simulate_calendar(
                data,
                segments,
                seed=sim_seed,
                horizon_days=args.horizon,
                background_per_service_day=args.background_rate,
                concentration=args.concentration,
            )
        table, svc = evaluate.build_tables(
            results, data["services"], outcome.final_status, segments, args.horizon, reference
        )
        rng = np.random.default_rng(sim_seed)
        result = evaluate.evaluate(table, svc, rng, args.bootstrap)
        result["events"] = dict(sorted(outcome.events.items()))
        report["scenarios"][scenario] = result
        tables[scenario] = table
        _log(f"{scenario}: будущее и метрики за {time.perf_counter() - t0:.1f} с")
    report["elapsed_s"] = round(time.perf_counter() - started, 1)
    return report, tables


def robustness_row(label: str, args: argparse.Namespace, report: dict[str, Any]) -> dict[str, Any]:
    scenarios = {}
    for name, sc in report["scenarios"].items():
        high = next(row for row in sc["levels"] if row["level"] == "high")
        scenarios[name] = {
            "roc_auc": sc["ranking"]["churned"]["model"]["roc_auc"],
            "pr_auc": sc["ranking"]["churned"]["model"]["pr_auc"],
            "churn_lift_high": high.get("churn_lift", math.nan),
            "base_churned": sc["base_rates"]["churned"],
        }
    return {
        "label": label,
        "seed": args.seed,
        "stream_rate": args.stream_rate,
        "concentration": args.concentration,
        "scenarios": scenarios,
    }


def run_robustness(args: argparse.Namespace, main_report: dict[str, Any]) -> list[dict[str, Any]]:
    """Основной прогон и несколько соседних: другие seed, темп потока и разброс поведения."""
    rows = [robustness_row(f"seed {args.seed} (основной)", args, main_report)]
    for label, overrides, scenarios in ROBUSTNESS:
        variant = argparse.Namespace(**{**vars(args), **overrides, "bootstrap": 0})
        variant.scenarios = [name for name in scenarios if name in args.scenarios]
        if not variant.scenarios:
            continue
        _log(f"устойчивость: {label}")
        report, _ = run(variant)
        rows.append(robustness_row(label, variant, report))
    return rows


# --- вывод ----------------------------------------------------------------------------------------


def _f(value: Any, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value:.{digits}f}"


def _pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{100 * value:.1f} %"


def _eur(cents: float) -> str:
    return f"€{cents / 100:,.0f}".replace(",", " ")


def ranking_table(ranking: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        "| Метод | ROC AUC | PR AUC | Precision@5% | Precision@10% | Recall@10% |",
        "|---|---|---|---|---|---|",
    ]
    for name, row in ranking.items():
        auc = _f(row["roc_auc"])
        pr = _f(row["pr_auc"])
        low = row.get("roc_auc_ci", [None])[0]
        if low is not None and not math.isnan(low):
            auc += f" [{_f(row['roc_auc_ci'][0])}–{_f(row['roc_auc_ci'][1])}]"
            pr += f" [{_f(row['pr_auc_ci'][0])}–{_f(row['pr_auc_ci'][1])}]"
        lines.append(
            f"| {NAMES.get(name, name)} | {auc} | {pr} | {_pct(row['precision_top5'])} | "
            f"{_pct(row['precision_top10'])} | {_pct(row['recall_top10'])} |"
        )
    return lines


def level_table(levels: list[dict[str, Any]]) -> list[str]:
    lines = [
        (
            "| Уровень | Клиентов | Доля базы | Ушли полностью | Lift | Потеряли ≥1 услугу | Lift | "
            "Прогноз доли потерь | Факт доли потерь |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in levels:
        if not row.get("n"):
            lines.append(f"| {LEVEL_NAMES[row['level']]} | 0 | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {LEVEL_NAMES[row['level']]} | {row['n']} | {_pct(row['share_of_base'])} | "
            f"{_pct(row['churn_rate'])} | {_f(row['churn_lift'], 2)} | {_pct(row['lost_any_rate'])} | "
            f"{_f(row['lost_any_lift'], 2)} | {_pct(row['predicted_share'])} | {_pct(row['actual_share'])} |"
        )
    return lines


def money_table(levels: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Уровень | Выручка за горизонт | expected_loss (прогноз) | Фактически потеряно | Факт / прогноз |",
        "|---|---|---|---|---|",
    ]
    for row in levels:
        if not row.get("n"):
            continue
        ratio = row["actual_loss"] / row["expected_loss"] if row["expected_loss"] else math.nan
        lines.append(
            f"| {LEVEL_NAMES[row['level']]} | {_eur(row['revenue'])} | {_eur(row['expected_loss'])} | "
            f"{_eur(row['actual_loss'])} | {_f(ratio, 2)} |"
        )
    return lines


def calibration_md(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Дециль | Клиентов | Score | Прогноз доли потерь | Факт доли потерь |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['group']} | {row['n']} | {row['score_min']:.1f}–{row['score_max']:.1f} | "
            f"{_pct(row['predicted_share'])} | {_pct(row['actual_share'])} |"
        )
    return lines


def robustness_md(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Прогон | stream ROC AUC | stream lift high | calendar ROC AUC | calendar lift high |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        cells = []
        for name in SCENARIOS:
            sc = row["scenarios"].get(name)
            cells += [_f(sc["roc_auc"]), _f(sc["churn_lift_high"], 2)] if sc else ["—", "—"]
        lines.append(f"| {row['label']} | " + " | ".join(cells) + " |")
    return lines


def to_markdown(report: dict[str, Any]) -> str:
    params = report["params"]
    out = [
        (
            f"Параметры: {params['accounts']} клиентов, seed {params['seed']}, T = {params['reference']}, "
            f"горизонт {params['horizon_days']} дн., riskcore {params['riskcore_version']} "
            f"(исходники {params['riskcore_sources']}), симулятор {params['simulator_sources']}."
        ),
        "",
    ]
    for name, sc in report["scenarios"].items():
        base = sc["base_rates"]
        svc = sc["service_level"]
        hard = sc["hard_subset"]
        out += [
            f"## Сценарий {name}",
            "",
            (
                f"Оценено клиентов: {sc['accounts']}, действующих платных услуг: {sc['services']}. "
                f"Ушли полностью: {_pct(base['churned'])}, потеряли хотя бы одну услугу: {_pct(base['lost_any'])}, "
                f"потеряно выручки: {_pct(base['revenue_lost_share'])}."
            ),
            "",
            "Цель «ушёл полностью» (все действующие на T услуги удалены к T + горизонт):",
            "",
            *ranking_table(sc["ranking"]["churned"]),
            "",
            "Цель «потерял хотя бы одну услугу»:",
            "",
            *ranking_table(sc["ranking"]["lost_any"]),
            "",
            "По уровням риска:",
            "",
            *level_table(sc["levels"]),
            "",
            "Деньги:",
            "",
            *money_table(sc["levels"]),
            "",
            "Калибровка по децилям score (доли взвешены выручкой):",
            "",
            *calibration_md(sc["calibration"]),
            "",
            (
                f"Уровень услуг: ROC AUC {_f(svc['roc_auc'])}, PR AUC {_f(svc['pr_auc'])}, "
                f"Brier {_f(svc['brier'])}, средняя p {_pct(svc['mean_probability'])}, "
                f"факт {_pct(svc['lost_rate'])}. Потерянных услуг с оплаченным сроком дальше горизонта: "
                f"{_pct(svc['lost_paid_beyond_horizon'])}."
            ),
            "",
            (
                f"Трудная подвыборка ({hard['description']}): {hard['accounts']} клиентов, "
                f"ушли полностью {_pct(hard['base_rates']['churned'])}, "
                f"потеряли ≥1 услугу {_pct(hard['base_rates']['lost_any'])}."
            ),
            "",
            "Цель «потерял хотя бы одну услугу»:",
            "",
            *ranking_table(hard["ranking"]["lost_any"]),
            "",
            *level_table(hard["levels"]),
            "",
            f"События будущего: {', '.join(f'{k} {v}' for k, v in sc['events'].items())}.",
            "",
        ]
    if report.get("robustness"):
        out += ["## Устойчивость", "", *robustness_md(report["robustness"]), ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    for stream_ in (sys.stdout, sys.stderr):
        if hasattr(stream_, "reconfigure"):
            stream_.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    report, tables = run(args)
    if not args.no_robustness:
        report["robustness"] = run_robustness(args, report)
    args.out.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(json_safe(report), ensure_ascii=False, indent=2, allow_nan=False)
    (args.out / "results.json").write_bytes((payload + "\n").encode("utf-8"))
    markdown = to_markdown(report)
    (args.out / "tables.md").write_text(markdown + "\n", encoding="utf-8")
    if not args.no_plots:
        from . import plots

        written = plots.render_all(report, tables, args.img)
        _log("графики: " + ", ".join(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p) for p in written))
    print(markdown)
    return 0
