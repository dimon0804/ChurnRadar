"""api: рейтинг клиентов, карточка, сводка, настройки модели, прокси симулятора, метрики."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
import structlog
from fastapi import Body, Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from svckit.auth import User, make_current_user, make_require_admin, make_require_role
from svckit.bus import EventBus
from svckit.db import create_pool, db_ready, get_pool
from svckit.errors import ApiError, RequestIdMiddleware, install_error_handlers
from svckit.health import install_probes
from svckit.logs import configure_logging
from svckit.mail import MailConfig
from svckit.metrics import HttpMetricsMiddleware, check_metrics_token, render
from svckit.outbox import OutboxRelay

from app import actions, mail_templates, mailer, marketing, metrics, model_quality, model_settings, preview, queries
from app.config import settings
from app.docs import router as docs_router

log = structlog.get_logger()

FORWARDED_HEADERS = ("x-user-id", "x-user-name", "x-user-roles", "x-request-id")


current_user = make_current_user(settings)
CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(make_require_admin(current_user))]
# работа с клиентами (письма, рассылки, отметки) — маркетолог и админ; аналитик только смотрит
MarketerUser = Annotated[User, Depends(make_require_role(current_user, "marketer"))]
Pool = Annotated[Any, Depends(get_pool)]


def kick_relay(request: Request) -> None:
    """settings.changed уже лежит в outbox вместе с новой версией — будим relay."""
    relay: OutboxRelay | None = getattr(request.app.state, "relay", None)
    if relay is not None:
        relay.kick()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, "api")
    app.state.pool = await create_pool(
        settings.database_url, settings.db_pool_min, settings.db_pool_max,
        statement_timeout_ms=settings.db_statement_timeout_ms,
        idle_in_transaction_timeout_ms=settings.db_idle_in_transaction_timeout_ms,
    )
    app.state.bus = EventBus(settings.nats_url, producer="api")
    await app.state.bus.start(wait_s=settings.nats_wait_on_start_s)
    app.state.relay = OutboxRelay(
        app.state.pool, app.state.bus, model_settings.OUTBOX_TABLE,
        metrics=metrics.outbox, batch_size=settings.outbox_batch_size, interval_s=settings.outbox_interval_s,
        backoff_max_s=settings.outbox_backoff_max_s, retention_s=settings.outbox_retention_s,
    )
    app.state.relay.start()
    app.state.http = httpx.AsyncClient(base_url=settings.simulator_url, timeout=settings.simulator_timeout_s)
    app.state.scoring_http = httpx.AsyncClient(base_url=settings.scoring_url, timeout=settings.scoring_timeout_s)
    app.state.mail_worker = mailer.MailWorker(
        app.state.pool, app.state.mail_config, rate_per_s=settings.mail_rate_per_s,
        batch_size=settings.mail_batch_size, interval_s=settings.mail_poll_interval_s,
        followup_days=settings.mail_followup_days,
    )
    app.state.mail_worker.start()
    log.info("api_started")
    yield
    await app.state.mail_worker.stop()
    await app.state.scoring_http.aclose()
    await app.state.http.aclose()
    await app.state.relay.stop()
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
    app.state.mail_config = MailConfig.from_env()
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

    # объявлен до /customers/{customer_id}, иначе «export.csv» попадёт в customer_id
    @app.get("/api/v1/customers/export.csv", tags=["customers"])
    async def export_customers(
        _: CurrentUser,
        pool: Pool,
        sort: queries.SortField = "risk_score",
        order: queries.SortOrder = "desc",
        level: str | None = None,
        search: Annotated[str | None, Query(max_length=320)] = None,
        worklist: bool = False,
    ) -> Response:
        rows = await actions.export_rows(
            pool, sort=sort, order=order, levels=queries.parse_levels(level), search=search, worklist=worklist,
        )
        name = actions.export_filename(datetime.now(UTC))
        return Response(
            actions.render_csv(rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
        )

    @app.get("/api/v1/customers/{customer_id}", tags=["customers"])
    async def get_customer(customer_id: int, _: CurrentUser, pool: Pool) -> dict[str, Any]:
        return await queries.get_customer(pool, customer_id)

    # --- с кем связаться сегодня -------------------------------------------------------
    # Отмечать и писать клиентам может и аналитик: с очередью работает маркетинг, а отметка
    # не меняет ни данные, ни модель. Кто, когда и что отправил, видно в журнале клиента.
    @app.get("/api/v1/contact-today", tags=["actions"])
    async def contact_today(
        _: CurrentUser,
        pool: Pool,
        limit: Annotated[int, Query(ge=1, le=100)] = 5,
        marked: bool = False,
    ) -> dict[str, Any]:
        return await actions.contact_today(pool, limit=limit, marked=marked)

    @app.get("/api/v1/customers/{customer_id}/action", tags=["actions"])
    async def get_customer_action(customer_id: int, _: CurrentUser, pool: Pool) -> dict[str, Any]:
        return await actions.get_action(pool, customer_id)

    @app.put("/api/v1/customers/{customer_id}/action", tags=["actions"])
    async def put_customer_action(
        customer_id: int, user: MarketerUser, pool: Pool, update: Annotated[actions.ActionUpdate, Body()]
    ) -> dict[str, Any]:
        state = await actions.set_action(pool, customer_id, update, user.name, settings.mail_followup_days)
        log.info("customer_action_set", customer_id=customer_id, status=update.status, user=user.name)
        return state

    @app.delete("/api/v1/customers/{customer_id}/action", tags=["actions"])
    async def delete_customer_action(customer_id: int, user: MarketerUser, pool: Pool) -> dict[str, Any]:
        state = await actions.clear_action(pool, customer_id, user.name)
        log.info("customer_action_cleared", customer_id=customer_id, user=user.name)
        return state

    # --- письма клиентам -------------------------------------------------------------
    def mail_config(request: Request) -> MailConfig:
        return request.app.state.mail_config

    def reply_to(request: Request) -> str:
        email = request.headers.get("x-user-email") if settings.auth_trust_headers else None
        return mailer.reply_to_for(email, settings.smtp_reply_to, mail_config(request))

    @app.get("/api/v1/mail/templates", tags=["mail"])
    async def get_mail_templates(_: CurrentUser, pool: Pool) -> dict[str, Any]:
        return await mail_templates.list_templates(pool)

    @app.put("/api/v1/mail/templates/{code}", tags=["mail"])
    async def put_mail_template(
        code: str, user: AdminUser, pool: Pool, patch: Annotated[mail_templates.TemplatePatch, Body()]
    ) -> dict[str, Any]:
        return await mail_templates.save_template(pool, code, patch, user.name)

    @app.post("/api/v1/mail/templates/{code}/reset", tags=["mail"])
    async def reset_mail_template(code: str, user: AdminUser, pool: Pool) -> dict[str, Any]:
        return await mail_templates.reset_template(pool, code, user.name)

    @app.post("/api/v1/customers/{customer_id}/email/draft", tags=["mail"])
    async def draft_customer_email(
        request: Request, customer_id: int, _: MarketerUser, pool: Pool,
        body: Annotated[mailer.EmailDraft, Body()],
    ) -> dict[str, Any]:
        return await mailer.draft(pool, customer_id, body, mail_config(request), reply_to(request))

    @app.post("/api/v1/customers/{customer_id}/email", tags=["mail"])
    async def send_customer_email(
        request: Request, customer_id: int, user: MarketerUser, pool: Pool,
        body: Annotated[mailer.EmailSend, Body()],
    ) -> dict[str, Any]:
        return await mailer.send_one(
            pool, customer_id, body, mail_config(request), reply_to(request), user.name, settings.mail_followup_days
        )

    @app.post("/api/v1/mail/campaigns/preview", tags=["mail"])
    async def preview_mail_campaign(
        request: Request, _: MarketerUser, pool: Pool, body: Annotated[mailer.CampaignRequest, Body()]
    ) -> dict[str, Any]:
        return await mailer.preview_campaign(pool, body, mail_config(request))

    @app.post("/api/v1/mail/campaigns", tags=["mail"], status_code=202)
    async def create_mail_campaign(
        request: Request, user: MarketerUser, pool: Pool, body: Annotated[mailer.CampaignRequest, Body()]
    ) -> dict[str, Any]:
        campaign = await mailer.create_campaign(pool, body, mail_config(request), reply_to(request), user.name)
        worker: mailer.MailWorker | None = getattr(request.app.state, "mail_worker", None)
        if worker is not None:
            worker.kick()
        return campaign

    @app.get("/api/v1/mail/campaigns", tags=["mail"])
    async def list_mail_campaigns(
        _: CurrentUser, pool: Pool, limit: Annotated[int, Query(ge=1, le=100)] = 20
    ) -> dict[str, Any]:
        return await mailer.list_campaigns(pool, limit)

    @app.get("/api/v1/mail/campaigns/{campaign_id}", tags=["mail"])
    async def get_mail_campaign(campaign_id: int, _: CurrentUser, pool: Pool) -> dict[str, Any]:
        return await mailer.get_campaign(pool, campaign_id)

    @app.get("/api/v1/mail/stats", tags=["mail"])
    async def get_mail_stats(_: CurrentUser, pool: Pool) -> dict[str, Any]:
        return await mailer.stats(pool, settings.mail_stats_days)

    # --- analytics ---------------------------------------------------------------
    @app.get("/api/v1/analytics/summary", tags=["analytics"])
    async def get_summary(request: Request, _: CurrentUser, pool: Pool) -> dict[str, Any]:
        return await request.app.state.summary_cache.get(pool)

    # Маркетинговые метрики видят все вошедшие; маржу и CAC меняет только admin.
    # Пересчёта они не запускают: применяются к готовым числам при выдаче.
    @app.get("/api/v1/analytics/marketing", tags=["analytics"])
    async def get_marketing(request: Request, _: CurrentUser, pool: Pool) -> dict[str, Any]:
        base, summary = await request.app.state.summary_cache.marketing(pool)
        return marketing.build_response(base, summary, await marketing.get_settings(pool))

    @app.get("/api/v1/analytics/marketing/settings", tags=["analytics"])
    async def get_marketing_settings(_: CurrentUser, pool: Pool) -> dict[str, Any]:
        return await marketing.get_settings(pool)

    @app.put("/api/v1/analytics/marketing/settings", tags=["analytics"])
    async def put_marketing_settings(
        user: AdminUser, pool: Pool, update: Annotated[marketing.MarketingSettingsUpdate, Body()]
    ) -> dict[str, Any]:
        state = await marketing.put_settings(pool, update, user.name)
        log.info("marketing_settings_changed", margin=state["margin"], cac=state["cac"], user=user.name)
        return state

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
            kick_relay(request)
        return state

    @app.post("/api/v1/model/settings/preview", tags=["model"])
    async def preview_model_settings(
        request: Request,
        user: AdminUser,
        pool: Pool,
        patch: Annotated[model_settings.ModelSettingsPatch, Body()],
    ) -> JSONResponse:
        return await preview.preview(request, user, pool, patch)

    @app.post("/api/v1/model/settings/reset", tags=["model"])
    async def reset_model_settings(request: Request, user: AdminUser, pool: Pool) -> dict[str, Any]:
        state, changed = await model_settings.reset_settings(pool, user.name)
        if changed:
            log.info("settings_reset", version=state["version"], user=user.name)
            kick_relay(request)
        return state

    @app.get("/api/v1/model/settings/history", tags=["model"])
    async def settings_history(
        _: CurrentUser, pool: Pool, limit: Annotated[int, Query(ge=1, le=200)] = 50
    ) -> dict[str, Any]:
        items = await model_settings.history(pool, limit)
        for item in items:
            item["changed_at"] = queries.iso_z(item["changed_at"])
        return {"items": items}

    @app.get("/api/v1/model/quality", tags=["model"])
    async def get_model_quality(_: CurrentUser) -> dict[str, Any]:
        try:
            return model_quality.load(settings.model_quality_path)
        except model_quality.QualityUnavailable as exc:
            log.warning("model_quality_unavailable", path=settings.model_quality_path, error=str(exc))
            raise ApiError(
                503, "model_quality_unavailable", "Результаты проверки модели не найдены в сборке api"
            ) from exc

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
                base, summary = await cache.marketing(pool)
                metrics.update_from_marketing(
                    marketing.build_response(base, summary, await marketing.get_settings(pool))
                )
        except Exception as exc:
            # отдаём последние известные значения, чтобы не терять серию
            log.warning("metrics_summary_failed", error=str(exc))
            if cache.last is not None:
                metrics.update_from_summary(cache.last)
        try:
            if pool is not None:
                metrics.update_mail_stats(await mailer.stats(pool, settings.mail_stats_days))
        except Exception as exc:
            log.warning("metrics_mail_failed", error=str(exc))
        return render(metrics.registry)

    return app


app = create_app()
