"""ingest: приём исходных данных (снимок и поток изменений) и публикация churn.source.changed.

Событие пишется в source.outbox той же транзакцией, что и данные, и уходит в NATS через relay:
недоступный NATS не теряет пересчёт.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import ctypes.util
import gc
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import asyncpg
import structlog
from fastapi import Depends, FastAPI, Request
from svckit.auth import User, make_current_user, make_require_admin
from svckit.body import RequestBodyMiddleware, parse_json_body, read_body
from svckit.bus import EventBus
from svckit.db import create_pool, db_ready, get_pool
from svckit.errors import ApiError, RequestIdMiddleware, install_error_handlers
from svckit.health import install_probes
from svckit.logs import configure_logging
from svckit.metrics import HttpMetricsMiddleware, check_metrics_token, render
from svckit.outbox import OutboxRelay

from app import metrics
from app.config import settings
from app.connectors import store as connector_store
from app.connectors.api import build_router as connectors_router
from app.connectors.http import RetryPolicy
from app.connectors.scheduler import Scheduler
from app.repository import OUTBOX_TABLE, WriteResult, apply_changes, replace_snapshot
from app.schemas import (
    SNAPSHOT,
    EventsAccepted,
    SnapshotAccepted,
    SourceChangeBatch,
    SourceSnapshot,
    check_batch_consistency,
)

log = structlog.get_logger()

current_user = make_current_user(settings)
require_admin = make_require_admin(current_user)
AdminUser = Annotated[User, Depends(require_admin)]
Pool = Annotated[Any, Depends(get_pool)]


def release_memory() -> None:
    """Возвращает ОС память после большого снимка: без этого glibc держит пик (гигабайты) до рестарта."""
    gc.collect()
    libc = ctypes.util.find_library("c")
    if libc:
        with contextlib.suppress(OSError, AttributeError):
            ctypes.CDLL(libc).malloc_trim(0)


def get_relay(request: Request) -> OutboxRelay | None:
    return getattr(request.app.state, "relay", None)


def kick_relay(relay: OutboxRelay | None) -> None:
    """Событие уже в outbox (закоммичено с данными) — будим relay, чтобы отправил без ожидания."""
    if relay is not None:
        relay.kick()


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
    return SNAPSHOT.validate_python(raw)


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
    kick_relay(getattr(app.state, "relay", None))
    return result


async def seed_when_schema_ready(app: FastAPI, attempts: int = 150, pause_s: float = 2.0) -> None:
    """Сид ждёт миграций: в Kubernetes job миграции и ingest стартуют одновременно."""
    for attempt in range(1, attempts + 1):
        try:
            await seed_if_empty(app)
            return
        except asyncpg.UndefinedTableError:
            if attempt == 1:
                log.info("seed_waiting_for_schema")
            await asyncio.sleep(pause_s)
        except Exception:
            log.exception("seed_failed")
            return
    log.error("seed_gave_up", reason="схема source не появилась")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, "ingest")
    app.state.pool = await create_pool(
        settings.database_url, settings.db_pool_min, settings.db_pool_max,
        statement_timeout_ms=settings.db_statement_timeout_ms,
        idle_in_transaction_timeout_ms=settings.db_idle_in_transaction_timeout_ms,
    )
    app.state.bus = EventBus(settings.nats_url, producer="ingest")
    await app.state.bus.start(wait_s=settings.nats_wait_on_start_s)
    app.state.relay = OutboxRelay(
        app.state.pool, app.state.bus, OUTBOX_TABLE,
        metrics=metrics.outbox, batch_size=settings.outbox_batch_size, interval_s=settings.outbox_interval_s,
        backoff_max_s=settings.outbox_backoff_max_s, retention_s=settings.outbox_retention_s,
    )
    # таблицы ещё может не быть (миграции идут параллельно) — relay подождёт её сам
    app.state.relay.start()
    seeding: asyncio.Task[None] | None = None
    try:
        await seed_if_empty(app)
    except asyncpg.UndefinedTableError:
        seeding = asyncio.create_task(seed_when_schema_ready(app))
    except Exception:
        log.exception("seed_failed")
    if settings.connectors_enabled:
        relay = app.state.relay
        app.state.scheduler = Scheduler(
            app.state.pool, settings.database_url,
            tick_s=settings.connectors_tick_s, run_timeout_s=settings.connector_run_timeout_s,
            http_timeout_s=settings.connector_http_timeout_s,
            retry=RetryPolicy(attempts=max(1, settings.connector_http_attempts),
                              base_delay_s=settings.connector_retry_base_s),
            failure_backoff_s=settings.connector_failure_backoff_s,
            allowed=settings.connector_allowed_hosts,
            on_written=lambda: kick_relay(relay),
            bootstrap=lambda conn: connector_store.bootstrap_from_settings(conn, settings),
        )
        app.state.scheduler.start()
    log.info("ingest_started")
    yield
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        await scheduler.stop()
    if seeding is not None:
        seeding.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await seeding
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
        title="ChurnRadar ingest",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan if with_lifespan else None,
    )
    install_error_handlers(app)
    app.add_middleware(
        RequestBodyMiddleware,
        max_bytes=settings.max_body_bytes,
        max_compressed_bytes=settings.max_compressed_body_bytes,
    )
    app.add_middleware(HttpMetricsMiddleware, histogram=metrics.http_duration)
    app.add_middleware(RequestIdMiddleware)

    @app.put("/api/v1/source/snapshot", status_code=202, response_model=SnapshotAccepted)
    async def put_snapshot(
        request: Request,
        user: AdminUser,
        pool: Pool,
        relay: Annotated[OutboxRelay | None, Depends(get_relay)] = None,
        reset_history: bool = False,
    ) -> SnapshotAccepted:
        # Тело разбираем сами: JSON сразу в датаклассы (pydantic-core), без промежуточных dict.
        # Content-Encoding: gzip к этому моменту уже распакован RequestBodyMiddleware.
        received = time.perf_counter()
        raw = await read_body(request)
        size = len(raw)
        parse_started = time.perf_counter()
        body = parse_json_body(request, raw, SNAPSHOT)
        del raw
        errors = check_batch_consistency(body.accounts, body.services, body.transactions)
        if errors:
            raise ApiError(422, "validation_error", "Снимок не прошёл проверку целостности", errors)
        started = time.perf_counter()
        metrics.parse_duration.observe(started - parse_started)
        result = await replace_snapshot(
            pool, body, kind="snapshot", created_by=user.name, reset_history=reset_history
        )
        assert result is not None  # None бывает только при only_if_empty
        written = time.perf_counter()
        metrics.write_duration.labels("snapshot").observe(written - started)
        _observe_write("snapshot", result)
        log.info(
            "snapshot_accepted", **result.counts, snapshot_id=result.batch_id, user=user.name, bytes=size,
            receive_s=round(parse_started - received, 2), parse_s=round(started - parse_started, 2),
            write_s=round(written - started, 2),
        )
        kick_relay(relay)
        del body
        release_memory()
        return SnapshotAccepted(snapshot_id=result.batch_id, counts=result.counts)

    @app.post("/api/v1/source/events", status_code=202, response_model=EventsAccepted)
    async def post_events(
        body: SourceChangeBatch,
        user: AdminUser,
        pool: Pool,
        relay: Annotated[OutboxRelay | None, Depends(get_relay)] = None,
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
        kick_relay(relay)
        return EventsAccepted(accepted=total)

    app.include_router(connectors_router(require_admin, lambda: settings.connector_allowed_hosts))

    install_probes(app, readiness)

    @app.get("/metrics", include_in_schema=False)
    async def get_metrics(request: Request):
        check_metrics_token(request, settings.metrics_token)
        return render(metrics.registry)

    return app


app = create_app()
