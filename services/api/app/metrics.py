"""Метрики Prometheus api: бизнес-показатели из scoring.summary и длительность HTTP-запросов.

Деньги в API — евроценты, в метриках *_eur — евро (float).

Распределение risk-score — снимок на момент последнего пересчёта, поэтому это Gauge с меткой
le, а не Histogram. Histogram в Prometheus — счётчики наблюдений, которые только растут: их
читают через rate()/increase(), а у нас после пересчёта число клиентов в корзине может и
уменьшиться, и rate() принял бы это за сброс счётчика. Накопленные корзины с le и "+Inf"
всё равно понимает histogram_quantile(), например histogram_quantile(0.5, churn_risk_score_bucket).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from prometheus_client import CollectorRegistry, Gauge
from svckit.metrics import http_duration_histogram
from svckit.outbox import OutboxMetrics

registry = CollectorRegistry(auto_describe=True)

# отправка churn.settings.changed из scoring.outbox
outbox = OutboxMetrics.create(registry)

customers_by_level = Gauge(
    "churn_customers_by_risk_level", "Клиенты по уровню риска", ["level"], registry=registry
)
customers_total = Gauge("churn_customers_total", "Клиентов всего", registry=registry)
expected_loss_eur = Gauge("churn_expected_loss_eur", "Ожидаемая потеря за горизонт, евро", registry=registry)
exposure_eur = Gauge("churn_exposure_eur", "Выручка под риском за горизонт, евро", registry=registry)
services_at_risk = Gauge("churn_services_at_risk", "Услуги уровня medium и high", registry=registry)
services_total = Gauge("churn_services_total", "Действующие услуги", registry=registry)
monthly_revenue_eur = Gauge("churn_monthly_revenue_eur", "Месячная выручка с действующих услуг, евро", registry=registry)
revenue_at_risk_share = Gauge("churn_revenue_at_risk_share", "Доля выручки горизонта под угрозой", registry=registry)
revenue_at_risk_ratio = Gauge(
    "churn_revenue_at_risk_ratio",
    "Доля выручки горизонта, которую ожидаем потерять (0..1); то же, что revenue_at_risk_share в сводке",
    registry=registry,
)
risk_score_avg = Gauge("churn_risk_score_avg", "Средний risk-score оценённых клиентов, 0..100", registry=registry)
risk_score_max = Gauge("churn_risk_score_max", "Наибольший risk-score среди клиентов, 0..100", registry=registry)
risk_score_customers = Gauge(
    "churn_risk_score_customers", "Оценённые клиенты (с risk-score, без unknown)", registry=registry
)
risk_score_bucket = Gauge(
    "churn_risk_score_bucket",
    "Клиенты с risk-score не выше le (накопленные корзины, снимок последнего пересчёта)",
    ["le"],
    registry=registry,
)
model_version = Gauge("churn_model_version", "Версия настроек модели последнего расчёта", registry=registry)
last_scored = Gauge(
    "churn_last_scored_timestamp_seconds", "Время последнего расчёта, unix-секунды (0 — расчёта не было)",
    registry=registry,
)
http_duration = http_duration_histogram(registry)

LEVELS = ("high", "medium", "low", "unknown")
RISK_SCORE_BUCKETS = tuple(range(10, 101, 10))


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def update_from_summary(summary: dict[str, Any]) -> None:
    by_level = summary.get("by_level") or {}
    for level in LEVELS:
        customers_by_level.labels(level).set(_num(by_level.get(level)))
    customers_total.set(_num(summary.get("customers_total")))
    expected_loss_eur.set(_num(summary.get("expected_loss")) / 100)
    exposure_eur.set(_num(summary.get("exposure")) / 100)
    services_at_risk.set(_num(summary.get("services_at_risk")))
    services_total.set(_num(summary.get("services_total")))
    monthly_revenue_eur.set(_num(summary.get("monthly_revenue_total")) / 100)
    revenue_at_risk_share.set(_num(summary.get("revenue_at_risk_share")))
    revenue_at_risk_ratio.set(_num(summary.get("revenue_at_risk_share")))
    _update_risk_scores(summary.get("risk_scores") or {})
    model_version.set(_num(summary.get("model_version")))
    scored_at = summary.get("scored_at")
    ts = 0.0
    if scored_at:
        try:
            ts = datetime.fromisoformat(str(scored_at).replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = 0.0
    last_scored.set(ts)


def _le_label(le: float) -> str:
    return str(int(le)) if float(le).is_integer() else str(le)


def _update_risk_scores(stats: dict[str, Any]) -> None:
    """Распределение score из сводки. Сводка старого формата (без risk_scores) даёт нули."""
    scored = _num(stats.get("scored"))
    risk_score_customers.set(scored)
    risk_score_avg.set(_num(stats.get("avg")))
    risk_score_max.set(_num(stats.get("max")))
    counts: dict[str, float] = {}
    for bucket in stats.get("buckets") or []:
        try:
            counts[_le_label(float(bucket["le"]))] = _num(bucket.get("count"))
        except (KeyError, TypeError, ValueError):
            continue
    # Корзины выставляем всегда, даже пустые: панели и histogram_quantile ждут полный набор le.
    for le in RISK_SCORE_BUCKETS:
        risk_score_bucket.labels(str(le)).set(counts.get(str(le), 0.0))
    risk_score_bucket.labels("+Inf").set(scored)
