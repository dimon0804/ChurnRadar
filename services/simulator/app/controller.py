"""Управление потоком: фоновый цикл, генерация и загрузка снимка в ingest."""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import orjson
from svckit.logs import get_logger

from .config import Config
from .generator import generate
from .metrics import BATCHES, CHANGES, GENERATE_SECONDS, RUNNING, WORLD_RESETS
from .stream import Batch, World
from .timeutil import iso, utcnow_ms

log = get_logger("simulator.controller")

SERVICE_HEADERS = {"X-User-Name": "simulator", "X-User-Roles": "admin", "X-User-Id": "simulator"}

StateLoader = Callable[[World], Awaitable[None]]

# ответы, после которых пакет имеет смысл отправить ещё раз
RETRY_STATUSES = frozenset({429, 502, 503, 504})


class IngestError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Controller:
    def __init__(
        self,
        config: Config,
        client: httpx.AsyncClient,
        *,
        state_loader: StateLoader | None = None,
        world: World | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.state_loader = state_loader
        self.world = world or World(random.Random(), clock=utcnow_ms, demo_day_seconds=config.demo_day_seconds)
        self.running = False
        self.interval_ms = config.interval_ms
        self.events_emitted = 0
        self.last_error: str | None = None
        self.last_generate: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()

    def state(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "interval_ms": self.interval_ms,
            "events_emitted": self.events_emitted,
            "last_error": self.last_error,
            "last_generate": self.last_generate,
        }

    # --- поток --------------------------------------------------------------------------------

    async def set_running(self, running: bool) -> None:
        self.running = running
        RUNNING.set(1 if running else 0)
        if running and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._loop())
        self._wake.set()

    def set_interval(self, interval_ms: int) -> None:
        self.interval_ms = interval_ms
        self._wake.set()

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while self.running:
            try:
                await self.step()
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("stream_step_failed", error=str(exc))
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval_ms / 1000)

    async def step(self) -> int:
        """Один шаг потока: 1–N изменений одним пакетом в ingest. Возвращает число изменений.

        tick() меняет мир в памяти до отправки. Если пакет так и не принят, мир расходится
        с БД (а при таймауте неизвестно, применил ли его ingest), поэтому мир сбрасывается
        и следующий шаг перечитывает его из БД. Отправка идёт под замком, чтобы опоздавший
        пакет не лёг поверх нового снимка из generate_and_load.
        """
        async with self._lock:
            if not self.world.loaded and self.state_loader is not None:
                await self.state_loader(self.world)
            batch = self.world.tick(self.config.max_changes)
            if batch.empty:
                return 0
            payload = batch.to_payload()
            try:
                await self._post_events(payload)
            except BaseException as exc:
                # и отмена посреди отправки: дошёл ли пакет, неизвестно
                self.world.unload()
                WORLD_RESETS.inc()
                log.warning("stream_batch_lost", error=repr(exc), changes=batch.kinds,
                            reload=self.state_loader is not None)
                raise
        return self._account(batch, payload)

    async def _post_events(self, payload: dict[str, Any]) -> None:
        """POST пакета с повторами на сетевых ошибках, таймаутах и 429/502/503/504."""
        url = f"{self.config.ingest_url}/api/v1/source/events"
        attempts = self.config.events_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = await self.client.post(
                    url, json=payload, headers=SERVICE_HEADERS, timeout=self.config.events_timeout_s
                )
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise IngestError(0, f"ingest недоступен: {exc!r}") from exc
                log.info("stream_batch_retry", attempt=attempt, error=repr(exc))
            else:
                if response.status_code < 300:
                    return
                error = IngestError(
                    response.status_code, f"ingest ответил {response.status_code}: {response.text[:300]}"
                )
                if response.status_code not in RETRY_STATUSES or attempt == attempts:
                    raise error
                log.info("stream_batch_retry", attempt=attempt, status=response.status_code)
            await asyncio.sleep(self.config.events_retry_backoff_s * 2 ** (attempt - 1))

    def _account(self, batch: Batch, payload: dict[str, Any]) -> int:
        changes = len(batch.kinds)
        self.events_emitted += changes
        self.last_error = None
        BATCHES.inc()
        for kind in batch.kinds:
            CHANGES.labels(kind=kind).inc()
        log.info("stream_batch", changes=batch.kinds, accounts=len(payload["accounts"]),
                 services=len(payload["services"]), transactions=len(payload["transactions"]))
        return changes

    # --- генерация ----------------------------------------------------------------------------

    async def generate_and_load(self, accounts: int, seed: int | None) -> dict[str, Any]:
        seed = seed if seed is not None else random.randrange(1, 2**31)
        async with self._lock:
            started = time.perf_counter()
            reference = utcnow_ms()
            data = await asyncio.to_thread(generate, accounts, seed, reference)
            if self.config.snapshot_reference == "null":
                data["reference_time"] = None
            raw_size, body = await asyncio.to_thread(_dumps_gzip, data, self.config.snapshot_gzip_level)
            gen_s = time.perf_counter() - started
            log.info("generated", accounts=accounts, seed=seed, bytes=raw_size, gzip_bytes=len(body),
                     seconds=round(gen_s, 2))
            response = await self.client.put(
                f"{self.config.ingest_url}/api/v1/source/snapshot",
                params={"reset_history": "true"},
                content=body,
                headers={**SERVICE_HEADERS, "Content-Type": "application/json", "Content-Encoding": "gzip"},
                timeout=self.config.ingest_timeout_s,
            )
            del body
            if response.status_code == 413:
                raise IngestError(
                    413,
                    f"Снимок {raw_size // (1024 * 1024)} МБ больше лимита ingest. Увеличьте MAX_BODY_BYTES "
                    "(и MAX_COMPRESSED_BODY_BYTES) у ingest или уменьшите число аккаунтов.",
                )
            if response.status_code >= 300:
                raise IngestError(response.status_code, f"ingest ответил {response.status_code}: {response.text[:300]}")
            max_tx = max((t["id"] for t in data["transactions"]), default=0)
            self.world.load(data["accounts"], data["services"], max_tx)
            GENERATE_SECONDS.observe(time.perf_counter() - started)
            self.last_generate = {
                "accounts": len(data["accounts"]),
                "services": len(data["services"]),
                "transactions": len(data["transactions"]),
                "seed": seed,
                "reference_time": data.get("reference_time") or iso(reference),
                "bytes": raw_size,
            }
            self.last_error = None
            return self.last_generate


def _dumps_gzip(data: dict[str, Any], level: int) -> tuple[int, bytes]:
    """Снимок в компактный JSON и gzip: по сети уходит примерно в 9 раз меньше."""
    raw = orjson.dumps(data)
    return len(raw), gzip.compress(raw, compresslevel=level)
