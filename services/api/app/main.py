"""api: рейтинг клиентов, карточка, сводка, настройки модели, прокси симулятора, метрики."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
import structlog
from fastapi import Body, Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from svckit.auth import User, make_current_user, make_require_admin
from svckit.bus import EventBus
from svckit.db import create_pool, db_ready, get_pool
from svckit.errors import ApiError, RequestIdMiddleware, install_error_handlers
from svckit.health import install_probes
from svckit.logs import configure_logging
from svckit.metrics import HttpMetricsMiddleware, check_metrics_token, render

from app import metrics, model_settings, queries
from app.config import settings
from app.docs import router as docs_router

log = structlog.get_logger()

SUBJECT_SETTINGS_CHANGED = "churn.settings.changed"
FORWARDED_HEADERS = ("x-user-id", "x-user-name", "x-user-roles", "x-request-id")


current_user = make_current_user(settings)
CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(make_require_admin(current_user))]
Pool = Annotated[Any, Depends(get_pool)]


async def publish_settings_changed(request: Request, state: dict[str, Any]) -> None:
    bus: EventBus | None = getattr(request.app.state, "bus", None)
    if bus is None:
        return
    sent = await bus.publish(
        SUBJECT_SETTINGS_CHANGED,
        "settings.changed",
        {"version": state["version"], "updated_by": state.get("updated_by")},
    )
    if sent is None:
        log.error("settings_event_lost", version=state["version"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, "api")
    app.state.pool = await create_pool(settings.database_url, settings.db_pool_min, settings.db_pool_max)
    app.state.bus = EventBus(settings.nats_url, producer="api")
    await app.state.bus.start(wait_s=settings.nats_wait_on_start_s)
    app.state.http = httpx.AsyncClient(base_url=settings.simulator_url, timeout=settings.simulator_timeout_s)
    log.info("api_started")
    yield
    await app.state.http.aclose()
    await app.state.bus.close()
    await app.state.pool.close()


async def readiness(request: Request) -> dict[str, bool]:
    bus = getattr(request.app.state, "bus", None)
    return {
        "db": await db_ready(getattr(request.app.state, "pool", None)),
        "nats": bool(bus and bus.connected),
    }


def create_app(*, with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(
        title="ChurnRadar API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan if with_lifespan else None,
    )
    app.state.summary_cache = queries.SummaryCache(settings.summary_cache_ttl_s)
    install_error_handlers(app)
    app.add_middleware(HttpMetricsMiddleware, histogram=metrics.http_duration)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(docs_router)

    # --- customers ---------------------------------------------------------------
    @app.get("/api/v1/customers", tags=["customers"])
    async def list_customers(
        _: CurrentUser,
        pool: Pool,
        sort: queries.SortField = "risk_score",
        order: queries.SortOrder = "desc",
        level: str | None = None,
        search: Annotated[str | None, Query(max_length=320)] = None,
        page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 25,
    ) -> dict[str, Any]:
        return await queries.list_customers(
            pool, sort=sort, order=order, levels=queries.parse_levels(level),
            search=search, page=page, page_size=page_size,
        )

    @app.get("/api/v1/customers/{customer_id}", tags=["customers"])
    async def get_customer(customer_id: int, _: CurrentUser, pool: Pool) -> dict[str, Any]:
        return await queries.get_customer(pool, customer_id)

    # --- analytics ---------------------------------------------------------------
    @app.get("/api/v1/analytics/summary", tags=["analytics"])
    async def get_summary(request: Request, _: CurrentUser, pool: Pool) -> dict[str, Any]:
        return await request.app.state.summary_cache.get(pool)

    # --- model settings ------------------------------------------------------------
    @app.get("/api/v1/model/settings", tags=["model"])
    async def get_model_settings(_: CurrentUser, pool: Pool) -> dict[str, Any]:
        return await model_settings.get_settings(pool)

    @app.patch("/api/v1/model/settings", tags=["model"])
    async def patch_model_settings(
        request: Request,
        user: AdminUser,
        pool: Pool,
        patch: Annotated[model_settings.ModelSettingsPatch, Body()],
    ) -> dict[str, Any]:
        state, changed = await model_settings.patch_settings(pool, patch, user.name)
        if changed:
            log.info("settings_changed", version=state["version"], user=user.name)
            await publish_settings_changed(request, state)
        return state

    @app.post("/api/v1/model/settings/reset", tags=["model"])
    async def reset_model_settings(request: Request, user: AdminUser, pool: Pool) -> dict[str, Any]:
        state, changed = await model_settings.reset_settings(pool, user.name)
        if changed:
            log.info("settings_reset", version=state["version"], user=user.name)
            await publish_settings_changed(request, state)
        return state

    @app.get("/api/v1/model/settings/history", tags=["model"])
    async def settings_history(
        _: CurrentUser, pool: Pool, limit: Annotated[int, Query(ge=1, le=200)] = 50
    ) -> dict[str, Any]:
        items = await model_settings.history(pool, limit)
        for item in items:
            item["changed_at"] = queries.iso_z(item["changed_at"])
        return {"items": items}

    # --- simulator (прокси) ------------------------------------------------------------
    async def proxy_simulator(request: Request, method: str, body: dict[str, Any] | None) -> JSONResponse:
        client: httpx.AsyncClient | None = getattr(request.app.state, "http", None)
        if client is None:
            raise ApiError(503, "simulator_unavailable", "Симулятор недоступен")
        headers = {h: request.headers[h] for h in FORWARDED_HEADERS if h in request.headers}
        rid = getattr(request.state, "request_id", None)
        if rid:
            headers["x-request-id"] = rid
        try:
            resp = await client.request(method, "/api/v1/simulator", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ApiError(504, "simulator_timeout", "Симулятор не ответил вовремя") from exc
        except httpx.HTTPError as exc:
            log.warning("simulator_unreachable", error=str(exc))
            raise ApiError(503, "simulator_unavailable", "Симулятор недоступен") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ApiError(502, "bad_gateway", "Симулятор вернул некорректный ответ") from exc
        return JSONResponse(payload, status_code=resp.status_code)

    @app.get("/api/v1/simulator", tags=["simulator"])
    async def get_simulator(request: Request, _: CurrentUser) -> JSONResponse:
        return await proxy_simulator(request, "GET", None)

    @app.put("/api/v1/simulator", tags=["simulator"])
    async def put_simulator(
        request: Request, _: AdminUser, command: Annotated[dict[str, Any], Body()]
    ) -> JSONResponse:
        return await proxy_simulator(request, "PUT", command)

    # --- ops -------------------------------------------------------------------------
    install_probes(app, readiness)

    @app.get("/metrics", include_in_schema=False)
    async def get_metrics(request: Request):
        check_metrics_token(request, settings.metrics_token)
        cache: queries.SummaryCache = request.app.state.summary_cache
        pool = getattr(request.app.state, "pool", None)
        try:
            if pool is not None:
                metrics.update_from_summary(await cache.get(pool))
        except Exception as exc:
            # отдаём последние известные значения, чтобы не терять серию
            log.warning("metrics_summary_failed", error=str(exc))
            if cache.last is not None:
                metrics.update_from_summary(cache.last)
        return render(metrics.registry)

    return app


app = create_app()
