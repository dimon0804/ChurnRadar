"""Планировщик коннекторов: ровно один исполнитель при нескольких репликах ingest.

Лидерство — сессионная pg_try_advisory_lock на отдельном соединении. Пока соединение живо,
блокировку держит одна реплика; упала реплика или оборвалась связь с Postgres — блокировка
снимается сама, и её берёт следующая. Остальные реплики раз в тик пробуют её взять.

Каждый запуск дополнительно держит свою advisory-блокировку на коннектор (тоже на отдельном
соединении). Это страховка на момент смены лидера: старый лидер мог ещё не заметить, что
потерял связь, а новый уже начал. Двух одновременных запусков одного коннектора не будет.

Запуск: курсор и опции из source.connectors → запросы к источнику (повторы с паузой внутри
запуска, общий таймаут на запуск) → запись тем же путём, что и push (apply_changes и outbox) →
статус, курсор и следующий запуск. После неудачи пауза растёт вдвое, но не дольше интервала.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import asyncpg
import httpx
import structlog

from app import metrics
from app.repository import _clock_time

from . import store
from .billing import run_billing
from .http import RetryPolicy, SourceClient, SourceError
from .metrica import run_metrica
from .models import allowed_hosts, check_base_url, read_token, validate_options

log = structlog.get_logger()

LEADER_LOCK_KEY = 7_340_211_003
RUN_LOCK_CLASS = 7_340_211  # pg_try_advisory_lock(int4, int4): класс + hashtext(id)
GAUGES_EVERY_S = 15.0


class Scheduler:
    def __init__(
        self,
        pool: asyncpg.Pool,
        dsn: str,
        *,
        tick_s: float = 2.0,
        run_timeout_s: float = 900.0,
        http_timeout_s: float = 30.0,
        retry: RetryPolicy | None = None,
        failure_backoff_s: float = 30.0,
        allowed: str = "",
        instance: str | None = None,
        on_written: Callable[[], None] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        bootstrap: Callable[[asyncpg.Connection], Any] | None = None,
        today: Callable[[], date] | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._pool = pool
        self._dsn = dsn
        self._tick_s = tick_s
        self._run_timeout_s = run_timeout_s
        self._http_timeout_s = http_timeout_s
        self._retry = retry or RetryPolicy()
        self._failure_backoff_s = failure_backoff_s
        self._allowed = allowed_hosts(allowed)
        self.instance = instance or socket.gethostname()
        self._on_written = on_written
        self._transport = transport
        self._bootstrap = bootstrap
        self._bootstrapped = bootstrap is None
        self._today = today or (lambda: datetime.now(UTC).date())
        self._sleep = sleep
        self._conn: asyncpg.Connection | None = None
        self._client: httpx.AsyncClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._wake = asyncio.Event()
        self._gauges_at = 0.0
        self._gauge_ids: set[str] = set()
        self.is_leader = False

    # --- жизненный цикл -----------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._client = httpx.AsyncClient(transport=self._transport, follow_redirects=False)
            self._task = asyncio.create_task(self._loop(), name="connectors-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._step_down()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def kick(self) -> None:
        """Разбудить планировщик (нажали «Запустить сейчас»)."""
        self._wake.set()

    async def wait_idle(self) -> None:
        """Дождаться текущих запусков (для тестов)."""
        while self._runs:
            await asyncio.gather(*list(self._runs.values()), return_exceptions=True)

    # --- цикл --------------------------------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except asyncpg.UndefinedTableError:
                pass  # миграции ещё идут
            except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
                log.warning("connectors_db_error", error=str(exc), leader=self.is_leader)
                await self._step_down()
            except Exception:
                log.exception("connectors_tick_failed")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._tick_s)

    async def tick(self) -> None:
        conn = await self._leader_conn()
        if not self._bootstrapped and self._bootstrap is not None:
            await self._bootstrap(conn)
            self._bootstrapped = True
        if not self.is_leader:
            if await conn.fetchval("SELECT pg_try_advisory_lock($1)", LEADER_LOCK_KEY):
                self.is_leader = True
                metrics.connector_leader.set(1)
                log.info("connectors_leader_elected", instance=self.instance)
                await self._recover(conn)
        else:
            await conn.fetchval("SELECT 1")  # связь жива — блокировка наша
        if self.is_leader:
            for connector_id in await store.due_connectors(conn):
                running = self._runs.get(connector_id)
                if running is None or running.done():
                    self._runs[connector_id] = asyncio.create_task(self._run_guarded(connector_id))
        if time.monotonic() - self._gauges_at >= GAUGES_EVERY_S:
            await self.refresh_gauges(conn)

    async def _leader_conn(self) -> asyncpg.Connection:
        if self._conn is None or self._conn.is_closed():
            self._conn = await asyncpg.connect(self._dsn, timeout=10)
        return self._conn

    async def _step_down(self) -> None:
        was_leader = self.is_leader
        self.is_leader = False
        metrics.connector_leader.set(0)
        for task in list(self._runs.values()):
            task.cancel()
        if self._runs:
            await asyncio.gather(*self._runs.values(), return_exceptions=True)
        self._runs.clear()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None
        if was_leader:
            log.info("connectors_leader_released", instance=self.instance)

    async def _recover(self, conn: asyncpg.Connection) -> None:
        """Запуски в статусе running от прежнего исполнителя: если их блокировка свободна, они брошены."""
        for row in await store.abandoned_runs(conn):
            key = row["connector_id"]
            if await conn.fetchval("SELECT pg_try_advisory_lock($1, hashtext($2))", RUN_LOCK_CLASS, key):
                try:
                    await store.mark_abandoned(conn, row["id"], key)
                    log.warning("connector_run_abandoned", source=key, run_id=row["id"])
                finally:
                    await conn.execute("SELECT pg_advisory_unlock($1, hashtext($2))", RUN_LOCK_CLASS, key)

    async def refresh_gauges(self, conn: asyncpg.Connection) -> None:
        self._gauges_at = time.monotonic()
        seen: set[str] = set()
        for row in await store.gauge_rows(conn):
            source = row["id"]
            seen.add(source)
            metrics.connector_enabled.labels(source).set(1 if row["enabled"] else 0)
            metrics.connector_interval_seconds.labels(source).set(row["interval_s"])
            metrics.connector_last_success_timestamp.labels(source).set(row["fresh_at"].timestamp())
        for gone in self._gauge_ids - seen:
            for gauge in (metrics.connector_enabled, metrics.connector_interval_seconds,
                          metrics.connector_last_success_timestamp):
                with contextlib.suppress(KeyError):
                    gauge.remove(gone)
        self._gauge_ids = seen

    # --- запуск ------------------------------------------------------------------------------

    async def _run_guarded(self, connector_id: str) -> None:
        try:
            await self.run_once(connector_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("connector_run_crashed", source=connector_id)
        finally:
            self._runs.pop(connector_id, None)

    async def run_once(self, connector_id: str) -> bool:
        """Один запуск коннектора. False — не запускали (занят, выключен, удалён)."""
        conn = await asyncpg.connect(self._dsn, timeout=10)
        try:
            if not await conn.fetchval(
                "SELECT pg_try_advisory_lock($1, hashtext($2))", RUN_LOCK_CLASS, connector_id
            ):
                return False
            row = await store.get_connector(conn, connector_id)
            if row is None or not (row["enabled"] or row["run_requested_at"] is not None):
                return False
            run_id, trigger = await store.start_run(conn, row, self.instance)
            started = time.perf_counter()
            log.info("connector_run_started", source=connector_id, kind=row["kind"], trigger=trigger, run_id=run_id)
            client = self._source_client(row)
            try:
                outcome = await asyncio.wait_for(self._execute(row, client), timeout=self._run_timeout_s)
            except (SourceError, TimeoutError, ValueError) as exc:
                if isinstance(exc, TimeoutError):
                    message = f"Запуск не уложился в {int(self._run_timeout_s)} с"
                else:
                    message = str(exc)
                duration = time.perf_counter() - started
                await store.finish_error(
                    conn, connector_id, run_id, error=message, attempts=client.attempts if client else 0,
                    duration_ms=int(duration * 1000), backoff_base_s=self._failure_backoff_s,
                )
                metrics.connector_runs_total.labels(connector_id, "error").inc()
                metrics.connector_run_duration.labels(connector_id).observe(duration)
                log.warning("connector_run_failed", source=connector_id, run_id=run_id, error=message)
                return True
            except BaseException as exc:
                # отмена (смена лидера, остановка) или неожиданная ошибка — запуск не должен висеть в running
                message = "Запуск прерван: сервис останавливается" if isinstance(exc, asyncio.CancelledError) \
                    else f"Внутренняя ошибка: {type(exc).__name__}: {exc}"
                with contextlib.suppress(Exception):
                    await asyncio.shield(store.finish_error(
                        conn, connector_id, run_id, error=message, attempts=client.attempts if client else 0,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        backoff_base_s=self._failure_backoff_s,
                    ))
                metrics.connector_runs_total.labels(connector_id, "error").inc()
                raise
            duration = time.perf_counter() - started
            records, cursor, details, wrote, again = outcome
            await store.finish_ok(
                conn, connector_id, run_id, cursor=cursor, records=records, attempts=client.attempts,
                details=details, duration_ms=int(duration * 1000), run_again=again,
            )
            metrics.connector_runs_total.labels(connector_id, "ok").inc()
            metrics.connector_records_total.labels(connector_id).inc(records)
            metrics.connector_run_duration.labels(connector_id).observe(duration)
            metrics.connector_last_success_timestamp.labels(connector_id).set(time.time())
            if wrote and self._on_written is not None:
                self._on_written()
            log.info("connector_run_done", source=connector_id, run_id=run_id, records=records,
                     duration_s=round(duration, 2), **{k: v for k, v in details.items() if isinstance(v, int)})
            return True
        finally:
            with contextlib.suppress(Exception):
                await conn.close()  # закрытие снимает и advisory-блокировку запуска

    def _source_client(self, row: asyncpg.Record) -> SourceClient:
        assert self._client is not None, "планировщик не запущен"
        return SourceClient(
            self._client,
            row["base_url"],
            source=row["id"],
            token=read_token(row["token_env"]),
            auth_scheme="OAuth" if row["kind"] == "yandex_metrica" else "Bearer",
            timeout_s=self._http_timeout_s,
            policy=self._retry,
            sleep=self._sleep,
        )

    async def _execute(
        self, row: asyncpg.Record, client: SourceClient
    ) -> tuple[int, dict[str, Any], dict[str, Any], bool, bool]:
        problem = check_base_url(row["base_url"], self._allowed)
        if problem:
            raise SourceError(problem.capitalize())
        if row["token_env"] and read_token(row["token_env"]) is None:
            raise SourceError(f"Токен не задан: переменная окружения {row['token_env']} у ingest пуста")
        options = validate_options(row["kind"], store._json(row["options"]))
        cursor = store._json(row["cursor"])
        if row["kind"] == "billing_http":
            result = await run_billing(self._pool, client, cursor, options, created_by=f"connector:{row['id']}")
            return result.records, result.cursor, result.details, result.writes > 0, bool(result.details["truncated"])
        if not row["token_env"]:
            raise SourceError("Для Метрики нужен OAuth-токен: укажите переменную CONNECTOR_TOKEN_*")
        async with self._pool.acquire() as conn:
            as_of = await _clock_time(conn)
        metrica = await run_metrica(self._pool, client, cursor, options, as_of=as_of, today=self._today(),
                                    sleep=self._sleep)
        return metrica.records, metrica.cursor, metrica.details, bool(metrica.account_ids), False
