"""Фоновая отправка очереди alerts.deliveries с повторами по backoff."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Mapping, Sequence
from datetime import timedelta

import asyncpg
from svckit.logs import get_logger

from . import repository as repo
from .channels import Channel, DeliveryError
from .metrics import DROPPED, ERRORS, FAILED, PENDING, SENT

log = get_logger("alerts.delivery")

CLAIM_BATCH = 20
PRUNE_EVERY_S = 3600.0


def backoff_delay(backoff: Sequence[float], attempt: int) -> float:
    """Пауза после attempt-й неудачной попытки (с 1)."""
    if not backoff:
        return 0.0
    return backoff[min(max(attempt, 1), len(backoff)) - 1]


class Dispatcher:
    def __init__(
        self,
        pool: asyncpg.Pool,
        channels: Mapping[str, Channel],
        *,
        interval_s: float = 2.0,
        backoff_s: Sequence[float] = (5.0, 15.0, 60.0),
        max_attempts: int = 8,
        lease_s: float = 60.0,
        retention: timedelta = timedelta(days=30),
    ) -> None:
        self._pool = pool
        self._channels = dict(channels)
        self._interval = interval_s
        self._backoff = tuple(backoff_s)
        self._max_attempts = max(1, max_attempts)
        self._lease = lease_s
        self._retention = retention
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()

    def kick(self) -> None:
        """Новая пачка в очереди: не ждём следующего тика."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def run(self) -> None:
        last_prune = 0.0
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                while await self.run_once() and not self._stop.is_set():
                    pass
                PENDING.set(await repo.pending_count(self._pool))
                if time.monotonic() - last_prune > PRUNE_EVERY_S:
                    await repo.prune(self._pool, self._retention)
                    last_prune = time.monotonic()
            except Exception:
                log.exception("delivery_loop_failed")
                ERRORS.labels(stage="delivery").inc()

    async def run_once(self) -> int:
        """Одна порция доставок. Возвращает, сколько взято (0 — очередь пуста)."""
        batch = await repo.claim_deliveries(self._pool, CLAIM_BATCH, self._lease)
        for delivery in batch:
            await self._deliver(delivery)
        return len(batch)

    async def _deliver(self, delivery: repo.Delivery) -> None:
        channel = self._channels.get(delivery.channel)
        if channel is None:
            # канал выключили (убрали токен или URL), пока пачка ждала в очереди
            DROPPED.labels(channel=delivery.channel).inc()
            await repo.mark_failed(self._pool, delivery.id, "канал выключен в настройках", retry_in_s=None)
            log.warning("delivery_channel_disabled", channel=delivery.channel, delivery_id=delivery.id)
            return
        attempt = delivery.attempts + 1
        try:
            await channel.send(delivery.payload, batch_id=delivery.batch_id)
        except DeliveryError as exc:
            FAILED.labels(channel=delivery.channel).inc()
            give_up = not exc.retryable or attempt >= self._max_attempts
            retry_in = None if give_up else backoff_delay(self._backoff, attempt)
            await repo.mark_failed(self._pool, delivery.id, str(exc), retry_in_s=retry_in)
            if give_up:
                DROPPED.labels(channel=delivery.channel).inc()
            log.warning(
                "delivery_failed",
                channel=delivery.channel,
                delivery_id=delivery.id,
                attempt=attempt,
                retry_in_s=retry_in,
                error=str(exc),
            )
            return
        SENT.labels(channel=delivery.channel).inc()
        await repo.mark_sent(self._pool, delivery.id)
        log.info("delivery_sent", channel=delivery.channel, delivery_id=delivery.id, attempt=attempt)
