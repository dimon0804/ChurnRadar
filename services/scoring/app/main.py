"""Точка входа воркера пересчёта: HTTP /healthz и /metrics на 8001 + потребитель NATS."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import signal

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import repository as repo
from .config import Config
from .engine import Scorer
from .logs import get_logger, setup_logging
from .worker import Worker

log = get_logger("scoring")


class State:
    def __init__(self) -> None:
        self.pool = None
        self.worker: Worker | None = None
        self.ready = False


def create_http_app(config: Config, state: State) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        checks = {"database": False, "nats": False}
        if state.pool is not None:
            with contextlib.suppress(Exception):
                async with state.pool.acquire(timeout=2) as conn:
                    await conn.fetchval("SELECT 1", timeout=2)
                checks["database"] = True
        checks["nats"] = state.worker is not None and state.worker.nats_connected
        ok = all(checks.values()) and state.ready
        return JSONResponse({"status": "ok" if ok else "starting" if not state.ready else "degraded", **checks},
                            status_code=200 if ok else 503)

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        if config.metrics_token:
            header = request.headers.get("authorization", "")
            expected = f"Bearer {config.metrics_token}"
            if not hmac.compare_digest(header.encode(), expected.encode()):
                return JSONResponse(
                    {"error": {"code": "unauthorized", "message": "Нужен токен сборщика метрик", "details": None}},
                    status_code=401,
                )
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

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
    setup_logging(config.log_level)
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
