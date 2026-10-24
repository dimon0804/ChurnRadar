"""HTTP API симулятора: GET/PUT /api/v1/simulator, /healthz, /metrics. Порт 8003."""

from __future__ import annotations

import hmac
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from .config import Config
from .controller import Controller, IngestError
from .logs import get_logger, setup_logging
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


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def _error(request: Request, status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": details, "request_id": request_id}},
        status_code=status,
    )


@dataclass(frozen=True, slots=True)
class User:
    name: str
    roles: frozenset[str]


def make_user_dependency(config: Config):  # type: ignore[no-untyped-def]
    def current_user(request: Request) -> User:
        name = request.headers.get("x-user-name") if config.auth_trust_headers else None
        roles_raw = request.headers.get("x-user-roles", "") if name else ""
        if not name and config.auth_dev_user:
            name, roles_raw = config.auth_dev_user, config.auth_dev_roles
        if not name:
            raise ApiError(401, "unauthorized", "Нужна авторизация")
        roles = frozenset(r.strip() for r in roles_raw.split(",") if r.strip())
        return User(name=name, roles=roles)

    return current_user


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
    current_user = make_user_dependency(config)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error(request, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return _error(request, 422, "validation_error", "Некорректные параметры команды", details)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        if config.metrics_token:
            header = request.headers.get("authorization", "")
            if not hmac.compare_digest(header.encode(), f"Bearer {config.metrics_token}".encode()):
                return _error(request, 401, "unauthorized", "Нужен токен сборщика метрик")
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

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
    setup_logging(config.log_level)
    uvicorn.run(create_app(config), host=config.http_host, port=config.http_port, log_level="warning",
                access_log=False)


if __name__ == "__main__":
    main()
