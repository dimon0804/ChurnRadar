"""HTTP API симулятора: GET/PUT /api/v1/simulator, /healthz, /readyz, /metrics. Порт HTTP_PORT (8000).

Демо-источники для коннекторов ingest (только внутри сети стенда, через шлюз не открыты):
  /demo/billing/v1/{accounts|services|transactions} — HTTP API биллинга (contracts/billing-source.openapi.yaml);
  /demo/metrica/management/v1/counter/{id}/...     — фейковый Logs API Яндекс Метрики.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import asyncpg
import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from svckit.auth import User, make_current_user
from svckit.errors import ApiError
from svckit.health import install_probes
from svckit.logs import configure_logging, get_logger
from svckit.metrics import METRICS_TOKEN_MESSAGE, bearer_token_valid, render

from .billing_feed import ENTITIES, BadCursor
from .config import Config
from .controller import Controller, IngestError
from .metrica_fake import FakeMetrica, MetricaError
from .source_db import make_anchor_loader, make_ids_loader, make_loader

log = get_logger("simulator")


class GenerateCommand(BaseModel):
    model_config = ConfigDict(extra="ignore")
    accounts: int = Field(default=10_000, ge=1, le=200_000)
    seed: int | None = None


class SimulatorCommand(BaseModel):
    model_config = ConfigDict(extra="ignore")
    running: bool | None = None
    interval_ms: int | None = Field(default=None, ge=200, le=60_000)
    mode: Literal["push", "pull"] | None = None
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


def _token_ok(header: str | None, scheme: str, expected: str) -> bool:
    if not expected:
        return True
    prefix = f"{scheme} "
    if not header or not header.startswith(prefix):
        return False
    return hmac.compare_digest(header[len(prefix) :].strip().encode(), expected.encode())


def create_app(
    config: Config | None = None, controller: Controller | None = None, metrica: FakeMetrica | None = None
) -> FastAPI:
    config = config or Config.from_env()
    fake_metrica = metrica or FakeMetrica(
        accounts=make_ids_loader(config.database_url, config.demo_metrica_max_accounts),
        anchor=make_anchor_loader(config.database_url),
        counter_id=config.demo_metrica_counter,
        processing_s=config.demo_metrica_processing_s,
    )

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
        if command.mode is not None:
            ctrl.set_mode(command.mode)
            log.info("simulator_mode", user=user.name, mode=command.mode)
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

    @app.get("/api/v1/simulator/metrica/client-ids.csv")
    async def metrica_client_ids(user: User = Depends(current_user)) -> Response:
        if "admin" not in user.roles:
            raise ApiError(403, "forbidden", "Скачать соответствия может только администратор")
        return PlainTextResponse(
            await fake_metrica.client_ids_csv(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="metrica-client-ids.csv"'},
        )

    # --- демо-API биллинга (pull) ----------------------------------------------------------

    @app.get("/demo/billing/v1/{entity}")
    async def billing_page(
        entity: str,
        request: Request,
        updated_since: datetime | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> Any:
        if not _token_ok(request.headers.get("authorization"), "Bearer", config.demo_billing_token):
            return _error(request, 401, "unauthorized", "Нужен заголовок Authorization: Bearer <токен>")
        if entity not in ENTITIES:
            return _error(request, 404, "not_found", f"Нет сущности {entity}: есть {', '.join(ENTITIES)}")
        if updated_since is not None and updated_since.tzinfo is None:
            return _error(request, 400, "bad_request", "updated_since — время с часовым поясом (RFC 3339)")
        ctrl: Controller = request.app.state.controller
        await ctrl.ensure_loaded()
        try:
            return ctrl.feed.page(
                entity, updated_since=updated_since.astimezone(UTC) if updated_since else None,
                cursor=cursor, limit=limit,
            )
        except BadCursor as exc:
            return _error(request, 400, "bad_request", str(exc))

    # --- фейковый Logs API Яндекс Метрики --------------------------------------------------

    def metrica_denied(request: Request) -> JSONResponse | None:
        if _token_ok(request.headers.get("authorization"), "OAuth", config.demo_metrica_token):
            return None
        return JSONResponse(MetricaError(403, "invalid_token", "Неверный OAuth-токен").to_api(), status_code=403)

    def metrica_error(exc: MetricaError) -> JSONResponse:
        return JSONResponse(exc.to_api(), status_code=exc.status)

    base = "/demo/metrica/management/v1/counter/{counter_id}"

    @app.get(base + "/logrequests/evaluate")
    async def metrica_evaluate(
        counter_id: int, request: Request, date1: str = "", date2: str = "", fields: str = "", source: str = ""
    ) -> Any:
        if (denied := metrica_denied(request)) is not None:
            return denied
        try:
            return fake_metrica.evaluate(counter_id, date1, date2, fields, source)
        except MetricaError as exc:
            return metrica_error(exc)

    @app.post(base + "/logrequests")
    async def metrica_create(
        counter_id: int, request: Request, date1: str = "", date2: str = "", fields: str = "", source: str = ""
    ) -> Any:
        if (denied := metrica_denied(request)) is not None:
            return denied
        try:
            return fake_metrica.create(counter_id, date1, date2, fields, source)
        except MetricaError as exc:
            return metrica_error(exc)

    @app.get(base + "/logrequest/{request_id}")
    async def metrica_status(counter_id: int, request_id: int, request: Request) -> Any:
        if (denied := metrica_denied(request)) is not None:
            return denied
        try:
            return await fake_metrica.status(counter_id, request_id)
        except MetricaError as exc:
            return metrica_error(exc)

    @app.get(base + "/logrequest/{request_id}/part/{part}/download")
    async def metrica_download(counter_id: int, request_id: int, part: int, request: Request) -> Any:
        if (denied := metrica_denied(request)) is not None:
            return denied
        try:
            return PlainTextResponse(fake_metrica.download(counter_id, request_id, part))
        except MetricaError as exc:
            return metrica_error(exc)

    @app.post(base + "/logrequest/{request_id}/clean")
    async def metrica_clean(counter_id: int, request_id: int, request: Request) -> Any:
        if (denied := metrica_denied(request)) is not None:
            return denied
        try:
            return fake_metrica.clean(counter_id, request_id)
        except MetricaError as exc:
            return metrica_error(exc)

    return app


def main() -> None:
    import uvicorn

    config = Config.from_env()
    configure_logging(config.log_level, event_key="event", force=False)
    uvicorn.run(create_app(config), host=config.http_host, port=config.http_port, log_level="warning",
                access_log=False)


if __name__ == "__main__":
    main()
