"""Точка входа: HTTP (/healthz, /readyz, /metrics, /api/v1/alerts), потребитель NATS и отправка в каналы.

Без `from __future__ import annotations`: FastAPI должен видеть Depends(current_user)
из замыкания как объект, а не строку.
"""

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Sequence
from datetime import timedelta
from typing import Annotated, Any, Literal

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from svckit.auth import User, make_current_user
from svckit.db import db_ready
from svckit.errors import ApiError, RequestIdMiddleware, install_error_handlers
from svckit.health import install_probes
from svckit.logs import configure_logging, get_logger
from svckit.metrics import METRICS_TOKEN_MESSAGE, HttpMetricsMiddleware, bearer_token_valid, render

from . import repository as repo
from .channels import build_channels
from .config import Config
from .delivery import Dispatcher
from .metrics import ERRORS, HTTP_DURATION
from .worker import Worker

log = get_logger("alerts")

AlertKind = Literal["high_risk", "loss_jump", "total_loss_jump"]


class State:
    def __init__(self) -> None:
        self.pool: Any = None
        self.worker: Worker | None = None
        self.failed_task: str | None = None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def feed_item(row: dict[str, Any]) -> dict[str, Any]:
    cid = row.get("customer_id")
    return {
        "id": row["id"],
        "kind": row["kind"],
        "customer_id": cid,
        "email": row.get("email"),
        "risk_level": row.get("risk_level"),
        "prev_level": row.get("prev_level"),
        "expected_loss": row.get("expected_loss"),
        "prev_loss": row.get("prev_loss"),
        "message": row["message"],
        "created_at": _iso(row.get("created_at")),
        "link": f"/customers/{cid}" if cid is not None else "/customers?level=high",
    }


def create_http_app(config: Config, state: State) -> FastAPI:
    app = FastAPI(title="ChurnRadar Alerts", docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    app.add_middleware(HttpMetricsMiddleware, histogram=HTTP_DURATION)
    app.add_middleware(RequestIdMiddleware)
    current_user = make_current_user(config)

    async def readiness(_: Request) -> dict[str, bool]:
        worker = state.worker
        return {
            "database": await db_ready(state.pool),
            "nats": worker is not None and worker.nats_connected,
            "consumer": worker is not None and worker.subscribed,
            "background_tasks": state.failed_task is None,
        }

    install_probes(app, readiness)

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        if config.metrics_token and not bearer_token_valid(
            request.headers.get("authorization"), config.metrics_token, exact=True
        ):
            return JSONResponse(
                {"error": {"code": "unauthorized", "message": METRICS_TOKEN_MESSAGE, "details": None}},
                status_code=401,
            )
        return render()

    @app.get("/api/v1/alerts", tags=["alerts"])
    async def list_alerts(
        _: Annotated[User, Depends(current_user)],
        page: Annotated[int, Query(ge=1, le=100_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        customer_id: Annotated[int | None, Query(ge=1)] = None,
        kind: AlertKind | None = None,
    ) -> dict[str, Any]:
        if state.pool is None:
            raise ApiError(503, "service_unavailable", "База данных недоступна")
        rows, total, latest = await repo.list_feed(
            state.pool, page=page, page_size=page_size, customer_id=customer_id, kind=kind
        )
        return {
            "items": [feed_item(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "latest_id": latest,
            "channels": {"feed": True, "webhook": config.webhook_enabled, "telegram": config.telegram_enabled},
        }

    return app


async def _connect_with_retry(coro_factory, what: str, attempts: int = 60):  # type: ignore[no-untyped-def]
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            log.warning("connect_retry", target=what, attempt=attempt, error=str(exc))
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 10)
    raise RuntimeError(f"не удалось подключиться к {what}")


async def supervise(tasks: Sequence[asyncio.Task[Any]], stop: asyncio.Event, state: State) -> bool:
    """Ждёт сигнала остановки или падения фоновой задачи. True — задача упала."""
    stopper = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait({*tasks, stopper}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is stopper or task.cancelled():
                continue
            exc = task.exception()
            state.failed_task = task.get_name()
            log.error("background_task_failed", task=task.get_name(), error=repr(exc), exc_info=exc)
            ERRORS.labels(stage="task").inc()
            return True
        return False
    finally:
        stopper.cancel()


async def run() -> int:
    config = Config.from_env()
    configure_logging(config.log_level, event_key="event", force=False)
    # httpx пишет URL запросов в INFO, а в URL Telegram Bot API лежит токен
    logging.getLogger("httpx").setLevel(logging.WARNING)
    state = State()

    server = uvicorn.Server(
        uvicorn.Config(
            create_http_app(config, state),
            host=config.http_host,
            port=config.http_port,
            log_level="warning",
            access_log=False,
        )
    )
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    http_task = asyncio.create_task(server.serve())

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    state.pool = await _connect_with_retry(lambda: repo.create_pool(config.database_url), "postgres")
    client = httpx.AsyncClient(follow_redirects=False)
    channels = build_channels(config, client)
    dispatcher = Dispatcher(
        state.pool,
        channels,
        interval_s=config.delivery_interval_s,
        backoff_s=config.delivery_backoff_s,
        max_attempts=config.delivery_max_attempts,
        lease_s=config.delivery_lease_s,
        retention=timedelta(days=config.feed_retention_days),
    )
    worker = Worker(config, state.pool, on_enqueued=dispatcher.kick)
    state.worker = worker
    await _connect_with_retry(worker.connect, "nats")
    log.info("alerts_started", port=config.http_port, channels=["feed", *channels])

    tasks = [
        asyncio.create_task(worker.consume(), name="consume"),
        asyncio.create_task(dispatcher.run(), name="delivery"),
    ]
    failed = await supervise(tasks, stop, state)
    log.info("alerts_stopping", failed_task=state.failed_task)
    worker.stop()
    dispatcher.stop()
    alive = [t for t in tasks if not t.done()]
    if alive:
        _, still = await asyncio.wait(alive, timeout=config.shutdown_grace_s)
        for task in still:
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await worker.close()
    await client.aclose()
    await state.pool.close()
    server.should_exit = True
    await http_task
    return 1 if failed else 0


def main() -> None:
    # ненулевой код — чтобы оркестратор перезапустил контейнер
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
