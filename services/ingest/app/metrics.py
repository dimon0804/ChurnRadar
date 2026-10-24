"""Метрики Prometheus сервиса ingest."""

from __future__ import annotations

import hmac
import time

from fastapi import Request
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.errors import ApiError

registry = CollectorRegistry(auto_describe=True)

snapshots_total = Counter(
    "ingest_snapshots_total", "Принятые снимки исходных данных", ["kind"], registry=registry
)
change_batches_total = Counter(
    "ingest_change_batches_total", "Принятые пакеты изменений", registry=registry
)
records_total = Counter(
    "ingest_records_total", "Записанные записи исходных данных", ["entity"], registry=registry
)
status_changes_total = Counter(
    "ingest_status_changes_total", "Записи в истории статусов услуг", registry=registry
)
publish_failures_total = Counter(
    "ingest_publish_failures_total", "Не удалось опубликовать событие в NATS", registry=registry
)
write_duration = Histogram(
    "ingest_write_duration_seconds", "Время записи в БД", ["operation"], registry=registry,
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
http_duration = Histogram(
    "http_request_duration_seconds", "Длительность HTTP-запросов", ["method", "route", "status"],
    registry=registry,
)


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
