"""Графики для слайда: ROC, доля ушедших по уровням, калибровка, деньги."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from . import metrics
from .evaluate import LEVELS, Accounts

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
AQUA = "#1baf7a"
GRAY = "#9a9893"

SCENARIO_TITLES = {
    "stream": "Живой поток симулятора",
    "calendar": "Продления по календарю + скрытое поведение",
}
LEVEL_LABELS = {"low": "Низкий", "medium": "Средний", "high": "Высокий"}

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": TEXT_2,
        "axes.titlecolor": TEXT,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.labelsize": 11,
        "xtick.color": TEXT_2,
        "ytick.color": TEXT_2,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "legend.frameon": False,
        "font.size": 11,
        "font.family": "DejaVu Sans",
    }
)


def _pct_axis(ax: Any, axis: str = "y") -> None:
    fmt = matplotlib.ticker.PercentFormatter(1.0, decimals=0)
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def _save(fig: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def _panels(count: int, height: float = 4.6) -> tuple[Any, list[Any]]:
    fig, axes = plt.subplots(1, count, figsize=(6.2 * count, height), squeeze=False)
    return fig, list(axes[0])


def plot_roc(report: dict[str, Any], tables: dict[str, Accounts], path: Path) -> Path:
    fig, axes = _panels(len(tables), height=6.0)
    for ax, (name, table) in zip(axes, tables.items(), strict=True):
        y = table.churned
        ranking = report["scenarios"][name]["ranking"]["churned"]
        series = (
            ("model", table.score, BLUE, "Модель"),
            ("autoprolong_off", table.autoprolong_off_share, ORANGE, "Только «автопродление выкл.»"),
            ("prolong_wait", table.waiting_share, AQUA, "Только «ждёт оплаты»"),
        )
        for key, score, color, label in series:
            fpr, tpr = metrics.roc_curve(y, score)
            ax.plot(fpr, tpr, color=color, lw=2, label=f"{label}, AUC {ranking[key]['roc_auc']:.2f}")
        ax.plot([0, 1], [0, 1], color=GRAY, lw=1.2, ls="--", label="Случайно, AUC 0.50")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.01)
        ax.set_aspect("equal")
        ax.set_xlabel("Доля оставшихся, ошибочно попавших в список")
        ax.set_ylabel("Доля ушедших, найденных моделью")
        ax.set_title(SCENARIO_TITLES.get(name, name), loc="left")
        ax.legend(loc="lower right", fontsize=9.5)
    fig.suptitle("ROC: насколько хорошо score отделяет ушедших клиентов", x=0.01, ha="left", fontsize=15, color=TEXT)
    fig.tight_layout()
    return _save(fig, path)


def plot_levels(report: dict[str, Any], path: Path) -> Path:
    names = list(report["scenarios"])
    fig, axes = _panels(len(names))
    for ax, name in zip(axes, names, strict=True):
        rows = {r["level"]: r for r in report["scenarios"][name]["levels"]}
        base = rows["all"]["churn_rate"]
        values = [rows[lv].get("churn_rate", 0.0) or 0.0 for lv in LEVELS]
        x = np.arange(len(LEVELS))
        ax.bar(x, values, width=0.56, color=BLUE, zorder=3)
        top = max([*values, base]) or 1.0
        for xi, lv, v in zip(x, LEVELS, values, strict=True):
            row = rows[lv]
            if not row.get("n"):
                continue
            ax.text(
                xi,
                v + top * 0.03,
                f"{100 * v:.1f} %",
                ha="center",
                va="bottom",
                color=TEXT,
                fontsize=12,
                fontweight="bold",
            )
            ax.text(
                xi,
                v + top * 0.12,
                f"×{row['churn_lift']:.1f} к среднему",
                ha="center",
                va="bottom",
                color=TEXT_2,
                fontsize=9.5,
            )
        ax.axhline(base, color=GRAY, ls="--", lw=1.2, zorder=2, label=f"В среднем по базе: {100 * base:.1f} %")
        ax.legend(loc="upper left", fontsize=9.5)
        ax.set_xticks(x, [f"{LEVEL_LABELS[lv]}\n{rows[lv].get('n', 0)} клиентов" for lv in LEVELS])
        ax.set_ylim(0, top * 1.3)
        _pct_axis(ax)
        ax.grid(axis="x", visible=False)
        ax.set_ylabel("Ушли полностью за 30 дней")
        ax.set_title(SCENARIO_TITLES.get(name, name), loc="left")
    fig.suptitle("Клиенты высокого риска действительно уходят чаще", x=0.01, ha="left", fontsize=15, color=TEXT)
    fig.tight_layout()
    return _save(fig, path)


def plot_calibration(report: dict[str, Any], path: Path) -> Path:
    names = list(report["scenarios"])
    fig, axes = _panels(len(names), height=5.2)
    for ax, name in zip(axes, names, strict=True):
        rows = report["scenarios"][name]["calibration"]
        pred = [r["predicted_share"] for r in rows]
        fact = [r["actual_share"] for r in rows]
        ax.plot([0, 1], [0, 1], color=GRAY, lw=1.2, ls="--", label="Идеальная калибровка")
        ax.plot(pred, fact, color=BLUE, lw=2, marker="o", ms=8, mec=SURFACE, mew=2, label="Децили score", zorder=3)
        for r in rows[-1:]:
            ax.annotate(
                f"дециль {r['group']}",
                (r["predicted_share"], r["actual_share"]),
                textcoords="offset points",
                xytext=(8, -12),
                color=TEXT_2,
                fontsize=9,
            )
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_aspect("equal")
        _pct_axis(ax, "x")
        _pct_axis(ax, "y")
        ax.set_xlabel("Прогноз: score / 100 (доля выручки под риском)")
        ax.set_ylabel("Факт: доля выручки, потерянная за 30 дней")
        ax.set_title(SCENARIO_TITLES.get(name, name), loc="left")
        ax.legend(loc="upper left", fontsize=9.5)
    fig.suptitle("Калибровка: прогноз доли потерь против факта", x=0.01, ha="left", fontsize=15, color=TEXT)
    fig.tight_layout()
    return _save(fig, path)


def plot_money(report: dict[str, Any], path: Path) -> Path:
    names = list(report["scenarios"])
    fig, axes = _panels(len(names))
    for ax, name in zip(axes, names, strict=True):
        rows = {r["level"]: r for r in report["scenarios"][name]["levels"]}
        x = np.arange(len(LEVELS))
        expected = [rows[lv].get("expected_loss", 0.0) / 100 for lv in LEVELS]
        actual = [rows[lv].get("actual_loss", 0.0) / 100 for lv in LEVELS]
        w = 0.36
        ax.bar(x - w / 2 - 0.01, expected, width=w, color=BLUE, label="Прогноз (expected_loss)", zorder=3)
        ax.bar(x + w / 2 + 0.01, actual, width=w, color=ORANGE, label="Факт", zorder=3)
        ax.set_xticks(x, [LEVEL_LABELS[lv] for lv in LEVELS])
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"€{v / 1000:.0f} тыс."))
        ax.grid(axis="x", visible=False)
        ax.set_ylabel("Потерянная выручка за 30 дней")
        ax.set_title(SCENARIO_TITLES.get(name, name), loc="left")
        ax.set_ylim(0, max([*expected, *actual]) * 1.25)
        ax.legend(loc="upper center", ncols=2, fontsize=9.5)
    fig.suptitle("Деньги: ожидаемые потери против фактических", x=0.01, ha="left", fontsize=15, color=TEXT)
    fig.tight_layout()
    return _save(fig, path)


def render_all(report: dict[str, Any], tables: dict[str, Accounts], directory: Path) -> list[Path]:
    return [
        plot_roc(report, tables, directory / "roc.png"),
        plot_levels(report, directory / "levels.png"),
        plot_calibration(report, directory / "calibration.png"),
        plot_money(report, directory / "money.png"),
    ]
