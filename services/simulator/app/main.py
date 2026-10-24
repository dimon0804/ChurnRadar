"""HTTP API симулятора: GET/PUT /api/v1/simulator, /healthz, /readyz, /metrics. Порт HTTP_PORT (8000)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from svckit.auth import User, make_current_user
from svckit.errors import ApiError
from svckit.health import install_probes
from svckit.logs import configure_logging, get_logger
from svckit.metrics import METRICS_TOKEN_MESSAGE, bearer_token_valid, render

from .config import Config
from .controller import Controller, IngestError
from .source_db import make_loader

log = get_logger("simulator")


class GenerateCommand(BaseModel):
    model_config = ConfigDict(extra="ignore")
    accounts: int = Field(default=10_000, ge=1, le=200_000)
    seed: int | None = None


class SimulatorCommand(BaseModel):
    model_config = ConfigDict(extra="ignore")
    running: bool | None = None
    interval_ms: int | None = Field(default=None, ge=200, le=60_000)
    generate: GenerateCommand | None = None


def _error(request: Request, status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": details, "request_id": request_id}},
        status_code=status,
    )


async def database_ready(dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(dsn, timeout=2)
    except (OSError, TimeoutError, asyncpg.PostgresError):
        return False
    try:
        return bool(await conn.fetchval("SELECT 1") == 1)
    except asyncpg.PostgresError:
        return False
    finally:
        await conn.close()


def create_app(config: Config | None = None, controller: Controller | None = None) -> FastAPI:
    config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        own_client = None
        ctrl = controller
        if ctrl is None:
            own_client = httpx.AsyncClient()
            ctrl = Controller(config, own_client, state_loader=make_loader(config.database_url))
        app.state.controller = ctrl
        if config.running:
            await ctrl.set_running(True)
        log.info("simulator_started", port=config.http_port, running=ctrl.running)
        try:
            yield
        finally:
            await ctrl.stop()
            if own_client is not None:
                await own_client.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # роли из заголовка сравниваются как есть, без приведения регистра
    current_user = make_current_user(config, lower_roles=False, unauthorized_message="Нужна авторизация")

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error(request, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return _error(request, 422, "validation_error", "Некорректные параметры команды", details)

    async def readiness(_: Request) -> dict[str, bool]:
        # Postgres нужен для стартового состояния потока; ingest проверяется своими пробами
        return {"database": await database_ready(config.database_url)}

    install_probes(app, readiness)

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        if config.metrics_token and not bearer_token_valid(
            request.headers.get("authorization"), config.metrics_token, exact=True
        ):
            return _error(request, 401, "unauthorized", METRICS_TOKEN_MESSAGE)
        return render()

    @app.get("/api/v1/simulator")
    async def get_state(request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
        return request.app.state.controller.state()

    @app.put("/api/v1/simulator")
    async def put_state(
        command: SimulatorCommand, request: Request, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        if "admin" not in user.roles:
            raise ApiError(403, "forbidden", "Управлять симулятором может только администратор")
        ctrl: Controller = request.app.state.controller
        if command.interval_ms is not None:
            ctrl.set_interval(command.interval_ms)
        if command.generate is not None:
            try:
                await ctrl.generate_and_load(command.generate.accounts, command.generate.seed)
            except IngestError as exc:
                ctrl.last_error = str(exc)
                status = 413 if exc.status == 413 else 502
                raise ApiError(status, "ingest_failed", str(exc)) from exc
            except httpx.HTTPError as exc:
                ctrl.last_error = f"ingest недоступен: {exc}"
                raise ApiError(502, "ingest_unavailable", "Сервис загрузки данных недоступен") from exc
            log.info("generate_done", user=user.name, **(ctrl.last_generate or {}))
        if command.running is not None:
            await ctrl.set_running(command.running)
            log.info("simulator_toggled", user=user.name, running=command.running)
        return ctrl.state()

    return app


def main() -> None:
    import uvicorn

    config = Config.from_env()
    configure_logging(config.log_level, event_key="event", force=False)
    uvicorn.run(create_app(config), host=config.http_host, port=config.http_port, log_level="warning",
                access_log=False)


if __name__ == "__main__":
    main()
