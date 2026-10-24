"""HTTP-запросы коннекторов к источникам: таймауты, повторы с экспоненциальной паузой, Retry-After.

Токен уходит только в заголовке Authorization и не попадает ни в логи, ни в текст ошибок.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app import metrics

log = structlog.get_logger()

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
Sleep = Callable[[float], Awaitable[None]]


class SourceError(Exception):
    """Источник не отдал данные. retryable — имеет смысл повторить запрос."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay_s)
        # ±20 %, чтобы реплики разных систем не били в источник одновременно
        jitter = random.uniform(0.8, 1.2)
        return min(self.base_delay_s * 2 ** (attempt - 1) * jitter, self.max_delay_s)


def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After: число секунд или HTTP-дата."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max((moment - (now or datetime.now(UTC))).total_seconds(), 0.0)


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class SourceClient:
    """Запросы к одному источнику. attempts считает все HTTP-попытки за запуск (для истории)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        *,
        source: str,
        token: str | None = None,
        auth_scheme: str = "Bearer",
        timeout_s: float = 30.0,
        policy: RetryPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._source = source
        self._headers = {"Accept": "application/json"}
        if token:
            self._headers["Authorization"] = f"{auth_scheme} {token}"
        self._timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, 10.0))
        self._policy = policy or RetryPolicy()
        self._sleep = sleep
        self.attempts = 0

    async def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        accept: str | None = None,
    ) -> httpx.Response:
        url = self._base + path
        headers = dict(self._headers)
        if accept:
            headers["Accept"] = accept
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(1, self._policy.attempts + 1):
            self.attempts += 1
            retry_after: float | None = None
            try:
                response = await self._client.request(method, url, params=clean, headers=headers, timeout=self._timeout)
            except httpx.TimeoutException:
                error = SourceError(f"{method} {path}: источник не ответил за {self._timeout.read:.0f} с", retryable=True)
            except httpx.TransportError as exc:
                error = SourceError(f"{method} {path}: источник недоступен ({type(exc).__name__})", retryable=True)
            else:
                if response.status_code < 400:
                    return response
                status = response.status_code
                if status in (401, 403):
                    message = f"{method} {path}: источник отказал в доступе ({status}), проверьте токен"
                else:
                    message = f"{method} {path}: источник ответил {status}: {_short(response.text)}"
                error = SourceError(message, retryable=status in RETRY_STATUSES, status=status)
                retry_after = retry_after_seconds(response.headers.get("Retry-After"))
            if not error.retryable or attempt == self._policy.attempts:
                raise error
            delay = self._policy.delay(attempt, retry_after)
            metrics.connector_http_retries_total.labels(self._source).inc()
            log.info("connector_http_retry", source=self._source, path=path, attempt=attempt,
                     delay_s=round(delay, 2), error=str(error))
            await self._sleep(delay)
        raise AssertionError("недостижимо")  # pragma: no cover

    async def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        response = await self.request("GET", path, params)
        return self.json(response, path)

    def json(self, response: httpx.Response, path: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError(f"{path}: ответ не JSON: {_short(response.text, 120)}") from exc
