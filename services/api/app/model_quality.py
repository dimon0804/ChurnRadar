"""Качество модели: результаты проверки на синтетическом будущем (tools/calibration).

Файл results.json считает `python -m tools.calibration`, в образ api он попадает при сборке
(см. Dockerfile). Здесь он проверяется по схеме, лишнее отбрасывается, NaN превращается в null.
Отдельно считается отпечаток исходников установленного riskcore: если он не совпал с тем,
на котором шла проверка, панель честно пишет, что цифры относятся к прошлой версии модели.
"""

from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Method = Literal["model", "autoprolong_off", "prolong_wait", "random", "segment_oracle"]
RocMethod = Literal["model", "autoprolong_off", "prolong_wait", "segment_oracle"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


Prob = float | None


class Ranking(_Model):
    roc_auc: Prob
    pr_auc: Prob
    precision_top5: Prob
    recall_top5: Prob
    precision_top10: Prob
    recall_top10: Prob
    roc_auc_ci: tuple[Prob, Prob] | None = None
    pr_auc_ci: tuple[Prob, Prob] | None = None


class Targets(_Model):
    churned: dict[Method, Ranking]
    lost_any: dict[Method, Ranking]


class LevelRow(_Model):
    level: Literal["low", "medium", "high", "all"]
    n: int
    share_of_base: Prob = None
    churn_rate: Prob = None
    churn_lift: Prob = None
    lost_any_rate: Prob = None
    lost_any_lift: Prob = None
    revenue: float | None = None
    expected_loss: float | None = None
    actual_loss: float | None = None
    predicted_share: Prob = None
    actual_share: Prob = None


class CalibrationRow(_Model):
    group: int
    n: int
    score_min: float
    score_max: float
    predicted_share: Prob
    actual_share: Prob


class BaseRates(_Model):
    churned: Prob
    lost_any: Prob
    revenue_lost_share: Prob = None


class Roc(_Model):
    target: Literal["churned", "lost_any"]
    fpr: list[float] = Field(min_length=2)
    tpr: dict[RocMethod, list[float]]


class PrecisionRecall(_Model):
    precision: Prob
    recall: Prob


class Threshold(_Model):
    flagged: int
    flagged_share: Prob
    churned: PrecisionRecall
    lost_any: PrecisionRecall


class Thresholds(_Model):
    high: Threshold
    medium_plus: Threshold


class HardSubset(_Model):
    description: str
    accounts: int
    base_rates: BaseRates
    ranking: Targets
    levels: list[LevelRow]


class Scenario(_Model):
    accounts: int
    services: int
    base_rates: BaseRates
    ranking: Targets
    levels: list[LevelRow]
    calibration: list[CalibrationRow]
    roc: Roc
    thresholds: Thresholds
    hard_subset: HardSubset


class Params(_Model):
    accounts: int
    seed: int
    reference: str
    horizon_days: int
    stream_rate: float
    background_rate: float
    concentration: float
    bootstrap: int
    riskcore_version: str
    riskcore_sources: str
    simulator_sources: str
    weights: dict[str, float]
    thresholds: dict[Literal["medium", "high"], float]
    groups: dict[str, float] = {}


class RobustnessScenario(_Model):
    roc_auc: Prob
    pr_auc: Prob
    churn_lift_high: Prob
    base_churned: Prob


class RobustnessRow(_Model):
    label: str
    seed: int
    stream_rate: float
    concentration: float
    scenarios: dict[Literal["stream", "calendar"], RobustnessScenario]


class HonestyMethod(_Model):
    service_roc_auc: Prob = None
    service_brier: Prob = None
    service_lift_top10: Prob = None
    mean_predicted: Prob = None
    actual_rate: Prob = None
    customer_roc_auc: Prob = None
    customer_lift_top10: Prob = None
    expected_loss: float | None = None
    actual_loss: float | None = None


class ReliabilityPoint(_Model):
    n: int
    predicted: float
    actual: float


class HonestyScenario(_Model):
    methods: dict[str, HonestyMethod]
    reliability: dict[str, list[ReliabilityPoint]]


class HonestyCalibration(_Model):
    fit_scenario: Literal["stream", "calendar"]
    services: int
    brier_cross: dict[str, dict[str, Prob]]
    by_scenario: dict[Literal["stream", "calendar"], HonestyScenario]
    logreg: dict[str, Any] | None = None


class GroupVariant(_Model):
    label: str
    cap: float | None
    level_changes: int
    levels: dict[str, int]
    expected_loss_raw: float | None = None
    scenarios: dict[str, dict[str, Prob]]


class Honesty(_Model):
    """Калибровка, челленджер и группы признаков (tools/calibration/honesty.py)."""

    calibration: HonestyCalibration
    groups: list[GroupVariant] = []
    case: dict[str, Any] | None = None
    tables_sha: str | None = None


class Report(_Model):
    """Та часть results.json, которую отдаёт API. Всё остальное (события, деньги по услугам) — в tables.md."""

    params: Params
    scenarios: dict[Literal["stream", "calendar"], Scenario]
    robustness: list[RobustnessRow] = []
    honesty: Honesty | None = None


class QualityUnavailable(Exception):
    """Файла с результатами нет или он не проходит проверку схемы."""


def _nan_to_none(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _nan_to_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_nan_to_none(v) for v in value]
    return value


def fingerprint(directory: Path) -> str:
    """Тот же отпечаток, что пишет tools/calibration/run.py: sha256 по *.py, первые 12 знаков."""
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*.py")):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:12]


def parse_report(raw: dict[str, Any]) -> dict[str, Any]:
    """Проверка по схеме и обрезка до того, что нужно панели."""
    return Report.model_validate(_nan_to_none(raw)).model_dump(mode="json")


@lru_cache(maxsize=1)
def current_tables_sha() -> str | None:
    """Отпечаток установленной таблицы калибровки — тот же, что пишет tools/calibration/honesty.py."""
    try:
        import riskcore
    except ImportError:
        return None
    path = Path(riskcore.__file__).resolve().parent / "model_tables.json"
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12]


@lru_cache(maxsize=1)
def current_riskcore_sources() -> str | None:
    try:
        import riskcore
    except ImportError:
        return None
    return fingerprint(Path(riskcore.__file__).resolve().parent)


@lru_cache(maxsize=4)
def load(path: str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QualityUnavailable(str(exc)) from exc
    try:
        report = parse_report(raw)
    except ValueError as exc:
        raise QualityUnavailable(
            f"results.json не прошёл проверку схемы: {exc}"
        ) from exc
    current = current_riskcore_sources()
    tables = current_tables_sha()
    expected_tables = (report.get("honesty") or {}).get("tables_sha")
    report["current_model"] = {
        "riskcore_sources": current,
        "matches": None
        if current is None
        else current == report["params"]["riskcore_sources"],
        "tables_sha": tables,
        "tables_match": None if tables is None or expected_tables is None else tables == expected_tables,
    }
    return report
