"""Метрики Prometheus api: бизнес-показатели из scoring.summary и длительность HTTP-запросов.

Деньги в API — евроценты, в метриках *_eur — евро (float).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from prometheus_client import CollectorRegistry, Gauge
from svckit.metrics import http_duration_histogram

registry = CollectorRegistry(auto_describe=True)

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
model_version = Gauge("churn_model_version", "Версия настроек модели последнего расчёта", registry=registry)
last_scored = Gauge(
    "churn_last_scored_timestamp_seconds", "Время последнего расчёта, unix-секунды (0 — расчёта не было)",
    registry=registry,
)
http_duration = http_duration_histogram(registry)

LEVELS = ("high", "medium", "low", "unknown")


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
    model_version.set(_num(summary.get("model_version")))
    scored_at = summary.get("scored_at")
    ts = 0.0
    if scored_at:
        try:
            ts = datetime.fromisoformat(str(scored_at).replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = 0.0
    last_scored.set(ts)
