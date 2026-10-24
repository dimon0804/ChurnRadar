"""Метрики Prometheus api: бизнес-показатели из scoring.summary и длительность HTTP-запросов.

Деньги в API — евроценты, в метриках *_eur — евро (float).
"""

from __future__ import annotations

import hmac
import time
from datetime import datetime
from typing import Any

from fastapi import Request
from prometheus_client import CollectorRegistry, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.errors import ApiError

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
http_duration = Histogram(
    "http_request_duration_seconds", "Длительность HTTP-запросов", ["method", "route", "status"],
    registry=registry,
)

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


def check_metrics_token(request: Request, token: str) -> None:
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip().encode(), token.encode()):
        raise ApiError(401, "unauthorized", "Нужен токен сборщика метрик")


def render() -> Response:
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


class HttpMetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            path = getattr(route, "path", None) or "unmatched"
            http_duration.labels(scope["method"], path, str(status["code"])).observe(
                time.perf_counter() - started
            )
