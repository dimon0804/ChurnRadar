"""Transactional outbox: событие ложится в БД той же транзакцией, что и данные, а в NATS его
отправляет фоновый relay. Упал NATS — событие ждёт в таблице и уходит, когда шина вернётся.

Таблица (см. db/core/002_outbox.sql): id, event_id, subject, type, payload, created_at,
published_at, attempts, last_error. Конверт собирается так же, как в EventBus.publish:
event_id и occurred_at берутся из строки, поэтому повторная отправка даёт тот же Nats-Msg-Id,
и JetStream отбрасывает дубль.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import asyncpg
import structlog
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge

from .bus import EventBus, envelope

log = structlog.get_logger()

_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


def _table(name: str) -> str:
    # имя таблицы подставляется в SQL, поэтому только schema.table из простых символов
    if not _TABLE_RE.match(name):
        raise ValueError(f"некорректное имя таблицы outbox: {name!r}")
    return name


async def add_event(
    conn: Any,
    table: str,
    subject: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    event_id: str | None = None,
) -> str:
    """Кладёт событие в outbox. Вызывать внутри транзакции, которая пишет сами данные.

    payload передаём текстом: так не важно, стоит ли на пуле json-кодек.
    """
    eid = event_id or str(uuid.uuid4())
    await conn.execute(
        f"INSERT INTO {_table(table)} (event_id, subject, type, payload) "
        "VALUES ($1::uuid, $2, $3, $4::text::jsonb)",
        eid,
        subject,
        event_type,
        json.dumps(payload, ensure_ascii=False),
    )
    return eid


@dataclass(slots=True)
class OutboxMetrics:
    pending: Gauge
    oldest_pending_s: Gauge
    published: Counter
    errors: Counter

    @classmethod
    def create(cls, registry: CollectorRegistry | None = None) -> OutboxMetrics:
        reg = registry if registry is not None else REGISTRY
        return cls(
            pending=Gauge(
                "outbox_pending_events", "События в outbox, ещё не отправленные в NATS", registry=reg
            ),
            oldest_pending_s=Gauge(
                "outbox_oldest_pending_seconds",
                "Возраст самого старого неотправленного события",
                registry=reg,
            ),
            published=Counter(
                "outbox_published_total", "События, отправленные из outbox в NATS", registry=reg
            ),
            errors=Counter(
                "outbox_publish_errors_total", "Неудачные попытки отправить событие", registry=reg
            ),
        )


@dataclass(slots=True)
class BatchResult:
    published: int = 0
    failed: bool = False
    more: bool = False


class OutboxRelay:
    """Фоновая отправка outbox в NATS.

    Пачку берёт `FOR UPDATE SKIP LOCKED`, так что несколько реплик сервиса не шлют одно и то же.
    Просыпается сразу после записи (kick) и раз в interval_s; при недоступном NATS ждёт
    с экспоненциальной паузой. Пока таблицы нет (миграции ещё идут), просто ждёт.
    """

    def __init__(
        self,
        pool: Any,
        bus: EventBus,
        table: str,
        *,
        metrics: OutboxMetrics | None = None,
        batch_size: int = 100,
        interval_s: float = 5.0,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 30.0,
        retention_s: float = 24 * 3600,
        cleanup_every_s: float = 600.0,
    ) -> None:
        self.pool = pool
        self.bus = bus
        self.table = _table(table)
        self.metrics = metrics
        self.batch_size = max(1, batch_size)
        self.interval_s = interval_s
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self.retention = timedelta(seconds=retention_s)
        self.cleanup_every_s = cleanup_every_s
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_cleanup = 0.0

    def kick(self) -> None:
        """Разбудить relay: в outbox появилось событие."""
        self._wake.set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name=f"outbox-relay:{self.table}")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def run_once(self) -> BatchResult:
        """Одна пачка: отправить по порядку, на первой ошибке остановиться (порядок важнее)."""
        if not self.bus.connected:
            # отправлять нечего — это не сбой, паузу не наращиваем
            return BatchResult(failed=await self.refresh_depth() > 0)
        result = BatchResult()
        async with self.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                f"SELECT id, event_id, subject, type, payload::text AS payload, created_at FROM {self.table} "
                "WHERE published_at IS NULL ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED",
                self.batch_size,
            )
            for row in rows:
                msg = envelope(
                    row["type"],
                    json.loads(row["payload"]),
                    self.bus.producer,
                    event_id=str(row["event_id"]),
                    occurred_at=row["created_at"],
                )
                if not await self.bus.publish_envelope(row["subject"], msg):
                    await conn.execute(
                        f"UPDATE {self.table} SET attempts = attempts + 1, last_error = $2 WHERE id = $1",
                        row["id"],
                        (self.bus.last_error or "NATS недоступен")[:500],
                    )
                    if self.metrics:
                        self.metrics.errors.inc()
                    result.failed = True
                    break
                # отмечаем сразу: соединение не простаивает в транзакции дольше одной публикации
                await conn.execute(
                    f"UPDATE {self.table} SET published_at = now(), attempts = attempts + 1, "
                    "last_error = NULL WHERE id = $1",
                    row["id"],
                )
                result.published += 1
            result.more = not result.failed and len(rows) == self.batch_size
        if self.metrics and result.published:
            self.metrics.published.inc(result.published)
        await self.refresh_depth()
        return result

    async def refresh_depth(self) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT count(*) AS n, EXTRACT(EPOCH FROM now() - min(created_at))::float8 AS age "
                f"FROM {self.table} WHERE published_at IS NULL"
            )
        pending = int(row["n"])
        if self.metrics:
            self.metrics.pending.set(pending)
            self.metrics.oldest_pending_s.set(row["age"] or 0.0)
        return pending

    async def cleanup(self) -> int:
        """Удаляет отправленные события старше retention."""
        async with self.pool.acquire() as conn:
            status = await conn.execute(
                f"DELETE FROM {self.table} WHERE published_at < now() - $1::interval", self.retention
            )
        removed = int(status.split()[-1]) if status else 0
        if removed:
            log.info("outbox_cleaned", table=self.table, removed=removed)
        return removed

    async def _wait(self, timeout_s: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=timeout_s)

    async def _backoff(self, delay_s: float) -> None:
        # пауза, но если NATS вернулся раньше — не досиживаем её
        was_connected = self.bus.connected
        deadline = time.monotonic() + delay_s
        while (left := deadline - time.monotonic()) > 0:
            await asyncio.sleep(min(left, 0.25))
            if not was_connected and self.bus.connected:
                return

    async def _loop(self) -> None:
        failures = 0
        waiting_schema = False
        while True:
            self._wake.clear()
            try:
                batch = await self.run_once()
                if waiting_schema:
                    log.info("outbox_schema_ready", table=self.table)
                    waiting_schema = False
                if (
                    not batch.failed
                    and not batch.more
                    and (time.monotonic() - self._last_cleanup >= self.cleanup_every_s)
                ):
                    self._last_cleanup = time.monotonic()
                    await self.cleanup()
            except asyncio.CancelledError:
                raise
            except asyncpg.UndefinedTableError:
                if not waiting_schema:
                    log.info("outbox_waiting_for_schema", table=self.table)
                    waiting_schema = True
                await self._wait(self.interval_s)
                continue
            except Exception as exc:
                log.warning("outbox_relay_error", table=self.table, error=str(exc))
                batch = BatchResult(failed=True)

            if batch.failed:
                failures += 1
                delay = min(self.backoff_base_s * 2 ** (failures - 1), self.backoff_max_s)
                if failures == 1 or failures % 10 == 0:
                    log.warning("outbox_publish_paused", table=self.table, failures=failures, retry_in=delay)
                await self._backoff(delay)
                continue
            if failures:
                log.info("outbox_publish_resumed", table=self.table, after_failures=failures)
            failures = 0
            if batch.more:
                continue
            await self._wait(self.interval_s)
