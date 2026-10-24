"""Общее для метрик Prometheus: токен сборщика, длительность HTTP-запросов, выдача /metrics."""

from __future__ import annotations

import hmac
import time

from fastapi import Request
from prometheus_client import REGISTRY, CollectorRegistry, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import ApiError

METRICS_TOKEN_MESSAGE = "Нужен токен сборщика метрик"


def bearer_token_valid(header: str | None, token: str, *, exact: bool = False) -> bool:
    """Проверка Authorization: Bearer <token> за постоянное время.

    exact=True — заголовок должен совпасть с "Bearer <token>" буква в букву;
    иначе схема без учёта регистра, а пробелы вокруг токена отбрасываются.
    """
    header = header or ""
    if exact:
        return hmac.compare_digest(header.encode(), f"Bearer {token}".encode())
    scheme, _, value = header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), token.encode())


def check_metrics_token(request: Request, token: str) -> None:
    if not bearer_token_valid(request.headers.get("Authorization"), token):
        raise ApiError(401, "unauthorized", METRICS_TOKEN_MESSAGE)


def http_duration_histogram(registry: CollectorRegistry | None = None) -> Histogram:
    return Histogram(
        "http_request_duration_seconds",
        "Длительность HTTP-запросов",
        ["method", "route", "status"],
        registry=registry if registry is not None else REGISTRY,
    )


def render(registry: CollectorRegistry | None = None) -> Response:
    return Response(
        generate_latest(registry if registry is not None else REGISTRY), media_type=CONTENT_TYPE_LATEST
    )


class HttpMetricsMiddleware:
    def __init__(self, app: ASGIApp, histogram: Histogram) -> None:
        self.app = app
        self.histogram = histogram

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            path = getattr(route, "path", None) or "unmatched"
            self.histogram.labels(scope["method"], path, str(status["code"])).observe(
                time.perf_counter() - started
            )
