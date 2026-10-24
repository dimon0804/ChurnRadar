"""Полный и инкрементальный пересчёт поверх riskcore."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import gc
import multiprocessing
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg
import riskcore
from riskcore import CustomerResult
from riskcore.types import Service, Settings

from . import repository as repo
from .logs import get_logger
from .metrics import CUSTOMERS, DURATION, RUNS

log = get_logger("scoring.engine")


def merged_settings(version: int, data: dict[str, Any]) -> Settings:
    """Хранимые настройки поверх реестра признаков: новые признаки получают веса по умолчанию."""
    return riskcore.merge_settings(data, version=version)


def now_ms() -> datetime:
    """Текущее время, усечённое до миллисекунд — как в колонках timestamptz(3)."""
    moment = datetime.now(UTC)
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


@dataclass(slots=True)
class RunResult:
    full: bool
    changed_ids: list[int]
    removed_ids: list[int]
    summary: dict[str, Any]
    model_version: int
    scored_at: datetime
    duration_ms: float
    customers: int
    timings: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class _Cache:
    """Результаты последнего расчёта в памяти — для инкрементального summary."""

    model_version: int
    scored_at: datetime
    results: dict[int, Any]
    services_by_owner: dict[int, list[Service]]


def _group_services(services: Sequence[Service]) -> dict[int, list[Service]]:
    grouped: dict[int, list[Service]] = {}
    for service in services:
        grouped.setdefault(service.owner_id, []).append(service)
    return grouped


def _result_id(result: CustomerResult) -> int:
    return int(result.customer_id)


class Scorer:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        lock_key: int,
        cache_results: bool = True,
        chunk_size: int = 5000,
        workers: int = 1,
    ) -> None:
        self._pool = pool
        self._chunk_size = max(1, chunk_size)
        self._workers = max(1, workers)
        self._process_pool: ProcessPoolExecutor | None = None
        self._vacuum_task: asyncio.Task[None] | None = None
        self.last_full_seconds = 0.0
        self._lock_key = lock_key
        self._cache_enabled = cache_results
        self._cache: _Cache | None = None
        self._local = asyncio.Lock()

    def _executor(self) -> ProcessPoolExecutor | None:
        """Пул процессов для полного пересчёта; при workers=1 — поток по умолчанию."""
        if self._workers <= 1:
            return None
        if self._process_pool is None:
            self._process_pool = ProcessPoolExecutor(
                max_workers=self._workers, mp_context=multiprocessing.get_context("spawn")
            )
        return self._process_pool

    def close(self) -> None:
        if self._process_pool is not None:
            self._process_pool.shutdown(wait=False, cancel_futures=True)
            self._process_pool = None

    def _schedule_vacuum(self) -> None:
        """Полная перезапись оставляет мёртвые строки; без VACUUM таблица пухнет от пересчёта к пересчёту.
        Запускаем в фоне, чтобы не задерживать публикацию результата: VACUUM не блокирует запись."""
        if self._vacuum_task is not None and not self._vacuum_task.done():
            return
        self._vacuum_task = asyncio.create_task(self._vacuum())

    async def _vacuum(self) -> None:
        started = time.perf_counter()
        try:
            async with self._pool.acquire() as conn:
                await repo.vacuum_scores(conn)
            log.info("vacuum_done", seconds=round(time.perf_counter() - started, 2))
        except Exception as exc:
            log.warning("vacuum_failed", error=str(exc))

    async def wait_maintenance(self) -> None:
        if self._vacuum_task is not None:
            await asyncio.gather(self._vacuum_task, return_exceptions=True)

    @property
    def busy(self) -> bool:
        return self._local.locked()

    async def run(
        self,
        *,
        full: bool,
        account_ids: Sequence[int] = (),
        event_ids: Sequence[str] = (),
        wait_lock: bool = True,
    ) -> RunResult | None:
        """Пересчёт под advisory lock. Без wait_lock возвращает None, если пересчёт уже идёт."""
        if not wait_lock and self._local.locked():
            return None
        async with self._local:
            async with self._pool.acquire() as conn:
                if wait_lock:
                    await conn.execute("SELECT pg_advisory_lock($1)", self._lock_key)
                elif not await conn.fetchval("SELECT pg_try_advisory_lock($1)", self._lock_key):
                    return None
                kind = "full" if full else "incremental"
                started = time.perf_counter()
                try:
                    result = await self._run_locked(conn, full=full, account_ids=account_ids, event_ids=event_ids)
                except Exception:
                    RUNS.labels(kind=kind, result="error").inc()
                    raise
                finally:
                    await conn.execute("SELECT pg_advisory_unlock($1)", self._lock_key)
                RUNS.labels(kind="full" if result.full else "incremental", result="ok").inc()
                DURATION.labels(kind="full" if result.full else "incremental").observe(time.perf_counter() - started)
                CUSTOMERS.set(result.customers)
        if result.full:
            self.last_full_seconds = result.duration_ms / 1000
            self._schedule_vacuum()
        return result

    async def _run_locked(
        self,
        conn: asyncpg.Connection,
        *,
        full: bool,
        account_ids: Sequence[int],
        event_ids: Sequence[str],
    ) -> RunResult:
        default_data = riskcore.default_settings().to_storage()
        version, data = await repo.ensure_settings(conn, default_data)
        if version == 1:
            await repo.ensure_settings_history(conn, version, data)
        settings = merged_settings(version, data)
        as_of, _ = await repo.resolve_as_of(conn)

        if not full and not await self._cache_valid(conn, version):
            full = True
        if full:
            return await self._full(conn, settings, version, as_of, event_ids)
        return await self._incremental(conn, settings, version, as_of, sorted(set(account_ids)), event_ids)

    async def _cache_valid(self, conn: asyncpg.Connection, version: int) -> bool:
        cache = self._cache
        if cache is None or cache.model_version != version:
            return False
        stamp = await repo.summary_stamp(conn)
        # Другая реплика могла записать результаты после нас — тогда кэш устарел.
        return stamp is not None and stamp == (cache.scored_at, cache.model_version)

    async def _full(
        self,
        conn: asyncpg.Connection,
        settings: Any,
        version: int,
        as_of: datetime,
        event_ids: Sequence[str],
    ) -> RunResult:
        """Полный пересчёт частями по chunk_size аккаунтов.

        Конвейер: одно соединение читает части в транзакции REPEATABLE READ (все части видят
        один снимок source.*), части считаются параллельно (процессы или поток), второе
        соединение в одной транзакции удаляет старые строки и пишет новые через COPY.
        Память ограничена несколькими частями в работе и облегчёнными результатами для сводки.
        """
        timings = {"load_s": 0.0, "score_s": 0.0, "write_s": 0.0}
        t0 = time.perf_counter()
        self._cache = None  # не держим два набора результатов в памяти одновременно
        scored_at = now_ms()
        slim: dict[int, Any] = {}
        services: list[Service] = []
        counts = {"services": 0, "transactions": 0}
        loop = asyncio.get_running_loop()
        executor = self._executor()
        queue: asyncio.Queue[tuple[asyncio.Future[Any], list[Service], int] | Exception | None] = asyncio.Queue(
            maxsize=max(2, self._workers + 1)
        )

        chunks = 0

        async def produce() -> None:
            nonlocal chunks
            try:
                started = time.perf_counter()
                async for data in repo.iter_source_chunks(conn, self._chunk_size):
                    timings["load_s"] += time.perf_counter() - started
                    chunks += 1
                    future = loop.run_in_executor(
                        executor, _score_chunk, data, settings, as_of, version, scored_at
                    )
                    await queue.put((future, data.services, len(data.transactions)))
                    started = time.perf_counter()
            except Exception as exc:
                await queue.put(exc)  # потребитель пробросит ошибку
                return
            await queue.put(None)

        async def consume(writer: asyncpg.Connection) -> None:
            while (item := await queue.get()) is not None:
                if isinstance(item, BaseException):
                    raise item
                future, chunk_services, tx_count = item
                started = time.perf_counter()
                results, records = await future
                timings["score_s"] += time.perf_counter() - started
                started = time.perf_counter()
                await repo.copy_scores(writer, records)
                timings["write_s"] += time.perf_counter() - started
                for result in results:
                    slim[_result_id(result)] = result
                services.extend(chunk_services)
                counts["services"] += len(chunk_services)
                counts["transactions"] += tx_count

        async with self._pool.acquire() as writer:
            async with conn.transaction(isolation="repeatable_read"), writer.transaction():
                history = await repo.load_status_history(conn, as_of)
                await repo.replace_all_scores_begin(writer)
                producer = asyncio.create_task(produce())
                try:
                    await consume(writer)
                finally:
                    if not producer.done():
                        producer.cancel()
                    await asyncio.gather(producer, return_exceptions=True)

                started = time.perf_counter()
                summary = await asyncio.to_thread(
                    _without_gc, riskcore.build_summary, list(slim.values()), services, history, settings, as_of
                )
                summary = _stamp_summary(summary, version, scored_at)
                timings["summary_s"] = time.perf_counter() - started
                await repo.save_summary(writer, summary, version, scored_at)
                await repo.mark_processed(writer, event_ids)
                await repo.prune_processed(writer)
        duration_ms = (time.perf_counter() - t0) * 1000

        if self._cache_enabled:
            self._cache = _Cache(
                model_version=version,
                scored_at=scored_at,
                results=slim,
                services_by_owner=_group_services(services),
            )
        # Кэш живёт до следующего полного пересчёта: убираем его из обходов циклического GC.
        gc.collect()
        gc.freeze()
        log.info(
            "rescore_done",
            kind="full",
            customers=len(slim),
            chunks=chunks,
            duration_ms=round(duration_ms, 1),
            **counts,
            **{k: round(v, 3) for k, v in timings.items()},
        )
        return RunResult(
            full=True,
            changed_ids=[],
            removed_ids=[],
            summary=summary,
            model_version=version,
            scored_at=scored_at,
            duration_ms=duration_ms,
            customers=len(slim),
            timings=timings,
        )

    async def _incremental(
        self,
        conn: asyncpg.Connection,
        settings: Any,
        version: int,
        as_of: datetime,
        account_ids: list[int],
        event_ids: Sequence[str],
    ) -> RunResult:
        cache = self._cache
        assert cache is not None
        t0 = time.perf_counter()
        source = await repo.load_source(conn, account_ids)
        history = await repo.load_status_history(conn, as_of)

        results = await asyncio.to_thread(
            riskcore.score_customers, source.accounts, source.services, source.transactions, settings, as_of
        )
        fresh = {_result_id(r): r for r in results}
        removed = [cid for cid in account_ids if cid not in fresh]
        for cid in removed:
            cache.results.pop(cid, None)
            cache.services_by_owner.pop(cid, None)
        cache.results.update({cid: _slim(r) for cid, r in fresh.items()})
        new_services = _group_services(source.services)
        for cid in account_ids:
            if cid in new_services:
                cache.services_by_owner[cid] = new_services[cid]
            else:
                cache.services_by_owner.pop(cid, None)

        all_results = list(cache.results.values())
        all_services = [s for group in cache.services_by_owner.values() for s in group]
        summary = await asyncio.to_thread(
            _without_gc, riskcore.build_summary, all_results, all_services, history, settings, as_of
        )
        scored_at = now_ms()
        summary = _stamp_summary(summary, version, scored_at)
        records = _records(results, version, scored_at)
        try:
            await repo.write_results(
                conn,
                records,
                full=False,
                removed_ids=removed,
                summary=summary,
                model_version=version,
                scored_at=scored_at,
                event_ids=event_ids,
            )
        except Exception:
            self._cache = None
            raise
        cache.scored_at = scored_at
        duration_ms = (time.perf_counter() - t0) * 1000
        log.info(
            "rescore_done",
            kind="incremental",
            customers=len(results),
            removed=len(removed),
            duration_ms=round(duration_ms, 1),
        )
        return RunResult(
            full=False,
            changed_ids=sorted(set(fresh) | set(removed)),
            removed_ids=removed,
            summary=summary,
            model_version=version,
            scored_at=scored_at,
            duration_ms=duration_ms,
            customers=len(cache.results),
        )


def _score_chunk(
    chunk: repo.SourceData, settings: Any, as_of: datetime, version: int, scored_at: datetime
) -> tuple[list[Any], list[tuple[Any, ...]]]:
    """Считает часть клиентов: облегчённые результаты для сводки и строки для COPY.

    Выполняется в потоке или в отдельном процессе, поэтому без замыканий и глобального состояния.
    """
    # Миллионы короткоживущих объектов: циклический GC здесь только мешает, всё освобождается счётчиком ссылок.
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        results = riskcore.score_customers(chunk.accounts, chunk.services, chunk.transactions, settings, as_of)
        records = _records(results, version, scored_at)
        return [_slim(r) for r in results], records
    finally:
        if gc_was_enabled:
            gc.enable()


def _without_gc(func: Any, *args: Any) -> Any:
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        return func(*args)
    finally:
        if gc_was_enabled:
            gc.enable()


def _slim(result: Any) -> Any:
    """Для сводки detail не нужен, а это основная часть памяти результата."""
    if getattr(result, "detail", None):
        with contextlib.suppress(TypeError, ValueError):
            return dataclasses.replace(result, detail={})
    return result


def _records(results: Sequence[Any], version: int, scored_at: datetime) -> list[tuple[Any, ...]]:
    return [repo.score_record(r.to_row(), version, scored_at) for r in results]


def _stamp_summary(summary: dict[str, Any], version: int, scored_at: datetime) -> dict[str, Any]:
    stamped = dict(summary)
    stamped["model_version"] = version
    stamped["scored_at"] = scored_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return stamped
