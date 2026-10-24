"""Публикация событий в NATS JetStream (стрим CHURN, subjects churn.>)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import nats
import structlog
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext
from nats.js.api import RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import NotFoundError

log = structlog.get_logger()

STREAM_NAME = "CHURN"
STREAM_SUBJECTS = ["churn.>"]
STREAM_MAX_AGE_S = 24 * 3600


def iso_z(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def envelope(
    event_type: str,
    payload: dict[str, Any],
    producer: str,
    *,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Конверт события (contracts/events/envelope.schema.json). Outbox передаёт свои id и время."""
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "type": event_type,
        "occurred_at": iso_z(occurred_at or datetime.now(UTC)),
        "schema_version": 1,
        "producer": producer,
        "payload": payload,
    }


async def ensure_stream(js: JetStreamContext, name: str = STREAM_NAME) -> None:
    """Создаёт стрим, если его ещё нет. Гонку с другим сервисом переживает."""
    try:
        await js.stream_info(name)
        return
    except NotFoundError:
        pass
    try:
        await js.add_stream(
            StreamConfig(
                name=name,
                subjects=STREAM_SUBJECTS,
                storage=StorageType.FILE,
                retention=RetentionPolicy.LIMITS,
                max_age=STREAM_MAX_AGE_S,
            )
        )
        log.info("nats_stream_created", stream=name)
    except Exception as exc:
        # стрим мог создать другой сервис параллельно
        await js.stream_info(name)
        log.info("nats_stream_exists", stream=name, note=str(exc))


class EventBus:
    def __init__(self, url: str, producer: str) -> None:
        self.url = url
        self.producer = producer
        self._nc: NATS | None = None
        self._js: JetStreamContext | None = None
        self._ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.last_error: str | None = None

    @property
    def connected(self) -> bool:
        return self._nc is not None and bool(self._nc.is_connected)

    async def start(self, wait_s: float = 0) -> None:
        """Подключение в фоне с повторами; при wait_s > 0 ждём готовности не дольше wait_s."""
        self._task = asyncio.create_task(self._connect_loop())
        if wait_s > 0:
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=wait_s)
            except TimeoutError:
                log.warning("nats_not_ready", url=self.url)

    async def _connect_loop(self) -> None:
        delay = 0.5
        while True:
            try:
                self._nc = await nats.connect(
                    self.url,
                    name=self.producer,
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=1,
                    connect_timeout=3,
                )
                self._js = self._nc.jetstream()
                await self.ensure_stream()
                self._ready.set()
                log.info("nats_connected", url=self.url)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("nats_connect_failed", error=str(exc), retry_in=delay)
                if self._nc is not None:
                    with contextlib.suppress(Exception):
                        await self._nc.close()
                    self._nc = None
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)

    async def ensure_stream(self) -> None:
        assert self._js is not None
        await ensure_stream(self._js)

    async def publish(
        self, subject: str, event_type: str, payload: dict[str, Any], retries: int = 3
    ) -> dict[str, Any] | None:
        """Публикует событие в конверте. Возвращает конверт или None, если не удалось."""
        msg = envelope(event_type, payload, self.producer)
        if await self.publish_envelope(subject, msg, retries=retries, wait_connect=True):
            return msg
        log.error("event_publish_gave_up", subject=subject, event_id=msg["event_id"])
        return None

    async def publish_envelope(
        self, subject: str, msg: dict[str, Any], *, retries: int = 1, wait_connect: bool = False
    ) -> bool:
        """Публикует готовый конверт с Nats-Msg-Id = event_id (JetStream отбрасывает повтор).

        wait_connect=False — без соединения сразу False: relay outbox сам решает, когда повторить.
        """
        data = json.dumps(msg, ensure_ascii=False).encode()
        for attempt in range(1, retries + 1):
            if self._js is None or not self.connected:
                self.last_error = "нет соединения с NATS"
                if not wait_connect:
                    break
                await asyncio.sleep(0.5 * attempt)
                continue
            try:
                await self._js.publish(subject, data, headers={"Nats-Msg-Id": msg["event_id"]}, timeout=5)
                log.info("event_published", subject=subject, event_id=msg["event_id"])
                return True
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("event_publish_failed", subject=subject, attempt=attempt, error=str(exc))
                if attempt < retries:
                    await asyncio.sleep(0.5 * attempt)
        return False

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._nc is not None:
            with contextlib.suppress(Exception):
                await self._nc.drain()
