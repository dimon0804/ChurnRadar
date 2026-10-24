"""Точка входа воркера пересчёта: HTTP /healthz, /readyz и /metrics на HTTP_PORT (8000) + потребитель NATS."""

from __future__ import annotations

import asyncio
import contextlib
import signal

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from svckit.db import db_ready
from svckit.health import install_probes
from svckit.logs import configure_logging, get_logger
from svckit.metrics import METRICS_TOKEN_MESSAGE, bearer_token_valid, render

from . import repository as repo
from .config import Config
from .engine import Scorer
from .worker import Worker

log = get_logger("scoring")


class State:
    def __init__(self) -> None:
        self.pool = None
        self.worker: Worker | None = None
        self.ready = False


def create_http_app(config: Config, state: State) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    async def readiness(_: Request) -> dict[str, bool]:
        worker = state.worker
        return {
            "database": await db_ready(state.pool),
            "nats": worker is not None and worker.nats_connected,
            "consumer": worker is not None and worker.subscribed,
            # первый полный пересчёт после старта завершён
            "initial_scoring": state.ready,
        }

    install_probes(app, readiness)

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        if config.metrics_token and not bearer_token_valid(
            request.headers.get("authorization"), config.metrics_token, exact=True
        ):
            return JSONResponse(
                {"error": {"code": "unauthorized", "message": METRICS_TOKEN_MESSAGE, "details": None}},
                status_code=401,
            )
        return render()

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


async def run() -> None:
    config = Config.from_env()
    configure_logging(config.log_level, event_key="event", force=False)
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

    state.pool = await _connect_with_retry(
        lambda: repo.create_pool(
            config.database_url,
            statement_timeout_ms=config.db_statement_timeout_ms,
            idle_in_transaction_timeout_ms=config.db_idle_in_transaction_timeout_ms,
        ),
        "postgres",
    )
    scorer = Scorer(
        state.pool,
        lock_key=config.advisory_lock_key,
        cache_results=config.cache_results,
        chunk_size=config.chunk_size,
        workers=config.workers,
    )
    worker = Worker(config, state.pool, scorer)
    state.worker = worker
    await _connect_with_retry(worker.connect, "nats")

    # Схему может ещё накатывать migrate — ждём, а не падаем.
    await _connect_with_retry(worker.initial, "scoring.initial", attempts=200)
    state.ready = True
    log.info("scoring_started", port=config.http_port)

    tasks = [asyncio.create_task(worker.consume()), asyncio.create_task(worker.periodic())]
    await stop.wait()
    log.info("scoring_stopping")
    worker.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await worker.close()
    await scorer.wait_maintenance()
    scorer.close()
    await state.pool.close()
    server.should_exit = True
    await http_task


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
