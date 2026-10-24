"""ingest: приём исходных данных (снимок и поток изменений) и публикация churn.source.changed."""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app import metrics
from app.auth import AdminUser
from app.body_limit import BodyLimitMiddleware
from app.bus import EventBus
from app.config import settings
from app.db import create_pool
from app.errors import ApiError, RequestIdMiddleware, install_error_handlers
from app.logging_setup import configure_logging
from app.repository import WriteResult, apply_changes, replace_snapshot
from app.schemas import (
    EventsAccepted,
    SnapshotAccepted,
    SourceChangeBatch,
    SourceSnapshot,
    check_batch_consistency,
)

log = structlog.get_logger()

SUBJECT_SOURCE_CHANGED = "churn.source.changed"


def get_pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise ApiError(503, "service_unavailable", "База данных недоступна")
    return pool


def get_bus(request: Request) -> EventBus | None:
    return getattr(request.app.state, "bus", None)


async def publish_source_changed(bus: EventBus | None, kind: str, result: WriteResult) -> None:
    payload = {
        "kind": kind,
        "snapshot_id": result.batch_id,
        "counts": result.counts,
        "account_ids": [] if kind == "snapshot" else result.account_ids,
    }
    sent = await bus.publish(SUBJECT_SOURCE_CHANGED, "source.changed", payload) if bus else None
    if sent is None:
        metrics.publish_failures_total.inc()


def _observe_write(kind: str, result: WriteResult) -> None:
    metrics.records_total.labels("accounts").inc(result.accounts)
    metrics.records_total.labels("services").inc(result.services)
    metrics.records_total.labels("transactions").inc(result.transactions)
    metrics.status_changes_total.inc(result.status_changes)
    if kind == "changes":
        metrics.change_batches_total.inc()
    else:
        metrics.snapshots_total.labels(kind).inc()


def load_seed(path: str) -> SourceSnapshot:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if settings.seed_reference_time is not None:
        raw["reference_time"] = settings.seed_reference_time.isoformat()
    return SourceSnapshot.model_validate(raw)


async def seed_if_empty(app: FastAPI) -> WriteResult | None:
    if not settings.seed_on_start:
        return None
    path = Path(settings.seed_path)
    if not path.is_file():
        log.warning("seed_file_missing", path=str(path))
        return None
    snap = load_seed(str(path))
    errors = check_batch_consistency(snap.accounts, snap.services, snap.transactions)
    if errors:
        log.error("seed_invalid", errors=errors[:10])
        return None
    result = await replace_snapshot(app.state.pool, snap, kind="seed", created_by="seed", only_if_empty=True)
    if result is None:
        log.info("seed_skipped", reason="source.accounts не пуст")
        return None
    _observe_write("seed", result)
    log.info("seed_loaded", **result.counts, snapshot_id=result.batch_id)
    await publish_source_changed(app.state.bus, "snapshot", result)
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, "ingest")
    app.state.pool = await create_pool(settings.database_url, settings.db_pool_min, settings.db_pool_max)
    app.state.bus = EventBus(settings.nats_url, producer="ingest")
    await app.state.bus.start(wait_s=settings.nats_wait_on_start_s)
    try:
        await seed_if_empty(app)
    except Exception:
        log.exception("seed_failed")
    log.info("ingest_started")
    yield
    await app.state.bus.close()
    await app.state.pool.close()


def create_app(*, with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(
        title="ChurnRadar ingest",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan if with_lifespan else None,
    )
    install_error_handlers(app)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(metrics.HttpMetricsMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.put("/api/v1/source/snapshot", status_code=202, response_model=SnapshotAccepted)
    async def put_snapshot(
        body: SourceSnapshot,
        user: AdminUser,
        pool=Depends(get_pool),
        bus: Annotated[EventBus | None, Depends(get_bus)] = None,
    ) -> SnapshotAccepted:
        errors = check_batch_consistency(body.accounts, body.services, body.transactions)
        if errors:
            raise ApiError(422, "validation_error", "Снимок не прошёл проверку целостности", errors)
        started = time.perf_counter()
        result = await replace_snapshot(pool, body, kind="snapshot", created_by=user.name)
        metrics.write_duration.labels("snapshot").observe(time.perf_counter() - started)
        _observe_write("snapshot", result)
        log.info("snapshot_accepted", **result.counts, snapshot_id=result.batch_id, user=user.name)
        await publish_source_changed(bus, "snapshot", result)
        return SnapshotAccepted(snapshot_id=result.batch_id, counts=result.counts)

    @app.post("/api/v1/source/events", status_code=202, response_model=EventsAccepted)
    async def post_events(
        body: SourceChangeBatch,
        user: AdminUser,
        pool=Depends(get_pool),
        bus: Annotated[EventBus | None, Depends(get_bus)] = None,
    ) -> EventsAccepted:
        total = len(body.accounts) + len(body.services) + len(body.transactions)
        if total == 0:
            return EventsAccepted(accepted=0)
        # дубли внутри пакета ловим до похода в БД
        local = [
            e for e in check_batch_consistency(body.accounts, body.services, body.transactions)
            if e["type"] == "duplicate"
        ]
        if local:
            raise ApiError(422, "validation_error", "Пакет изменений не прошёл проверку целостности", local)
        started = time.perf_counter()
        result = await apply_changes(pool, body, created_by=user.name)
        metrics.write_duration.labels("changes").observe(time.perf_counter() - started)
        _observe_write("changes", result)
        await publish_source_changed(bus, "changes", result)
        return EventsAccepted(accepted=total)

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request) -> JSONResponse:
        pool = getattr(request.app.state, "pool", None)
        bus = getattr(request.app.state, "bus", None)
        db_ok = False
        if pool is not None:
            try:
                async with pool.acquire(timeout=2) as conn:
                    db_ok = await conn.fetchval("SELECT 1") == 1
            except Exception:
                db_ok = False
        body = {"status": "ok" if db_ok else "fail", "checks": {"db": db_ok, "nats": bool(bus and bus.connected)}}
        return JSONResponse(body, status_code=200 if db_ok else 503)

    @app.get("/metrics", include_in_schema=False)
    async def get_metrics(request: Request):
        metrics.check_metrics_token(request, settings.metrics_token)
        return metrics.render()

    return app


app = create_app()
