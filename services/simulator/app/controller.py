"""Управление потоком: фоновый цикл, генерация и загрузка снимка в ingest."""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .config import Config
from .generator import generate
from .logs import get_logger
from .metrics import BATCHES, CHANGES, GENERATE_SECONDS, RUNNING
from .stream import World
from .timeutil import iso, utcnow_ms

log = get_logger("simulator.controller")

SERVICE_HEADERS = {"X-User-Name": "simulator", "X-User-Roles": "admin", "X-User-Id": "simulator"}

StateLoader = Callable[[World], Awaitable[None]]


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
        """Один шаг потока: 1–N изменений одним пакетом в ingest. Возвращает число изменений."""
        async with self._lock:
            if not self.world.loaded and self.state_loader is not None:
                await self.state_loader(self.world)
            batch = self.world.tick(self.config.max_changes)
            if batch.empty:
                return 0
            payload = batch.to_payload()
        response = await self.client.post(
            f"{self.config.ingest_url}/api/v1/source/events", json=payload, headers=SERVICE_HEADERS, timeout=30
        )
        if response.status_code >= 300:
            raise IngestError(response.status_code, f"ingest ответил {response.status_code}: {response.text[:300]}")
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
            body = await asyncio.to_thread(_dumps, data)
            gen_s = time.perf_counter() - started
            log.info("generated", accounts=accounts, seed=seed, bytes=len(body), seconds=round(gen_s, 2))
            response = await self.client.put(
                f"{self.config.ingest_url}/api/v1/source/snapshot",
                content=body,
                headers={**SERVICE_HEADERS, "Content-Type": "application/json"},
                timeout=self.config.ingest_timeout_s,
            )
            if response.status_code == 413:
                raise IngestError(
                    413,
                    f"Снимок {len(body) // (1024 * 1024)} МБ больше лимита ingest. "
                    "Увеличьте MAX_BODY_BYTES у ingest или уменьшите число аккаунтов.",
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
                "bytes": len(body),
            }
            self.last_error = None
            return self.last_generate


def _dumps(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
