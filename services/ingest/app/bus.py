"""Публикация событий в NATS JetStream (стрим CHURN, subjects churn.>)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import nats
import structlog
from nats.js.api import RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import NotFoundError

log = structlog.get_logger()

STREAM_NAME = "CHURN"
STREAM_SUBJECTS = ["churn.>"]
STREAM_MAX_AGE_S = 24 * 3600


def envelope(event_type: str, payload: dict[str, Any], producer: str) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "type": event_type,
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "schema_version": 1,
        "producer": producer,
        "payload": payload,
    }


class EventBus:
    def __init__(self, url: str, producer: str) -> None:
        self.url = url
        self.producer = producer
        self._nc: nats.NATS | None = None
        self._js = None
        self._ready = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

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
                    try:
                        await self._nc.close()
                    except Exception:
                        pass
                    self._nc = None
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)

    async def ensure_stream(self) -> None:
        assert self._js is not None
        try:
            await self._js.stream_info(STREAM_NAME)
        except NotFoundError:
            try:
                await self._js.add_stream(
                    StreamConfig(
                        name=STREAM_NAME,
                        subjects=STREAM_SUBJECTS,
                        storage=StorageType.FILE,
                        retention=RetentionPolicy.LIMITS,
                        max_age=STREAM_MAX_AGE_S,
                    )
                )
                log.info("nats_stream_created", stream=STREAM_NAME)
            except Exception as exc:
                # стрим мог создать другой сервис параллельно
                await self._js.stream_info(STREAM_NAME)
                log.info("nats_stream_exists", stream=STREAM_NAME, note=str(exc))

    async def publish(
        self, subject: str, event_type: str, payload: dict[str, Any], retries: int = 3
    ) -> dict[str, Any] | None:
        """Публикует событие в конверте. Возвращает конверт или None, если не удалось."""
        msg = envelope(event_type, payload, self.producer)
        data = json.dumps(msg, ensure_ascii=False).encode()
        for attempt in range(1, retries + 1):
            if self._js is None or not self.connected:
                await asyncio.sleep(0.5 * attempt)
                continue
            try:
                await self._js.publish(subject, data, headers={"Nats-Msg-Id": msg["event_id"]}, timeout=5)
                log.info("event_published", subject=subject, event_id=msg["event_id"])
                return msg
            except Exception as exc:
                log.warning("event_publish_failed", subject=subject, attempt=attempt, error=str(exc))
                await asyncio.sleep(0.5 * attempt)
        log.error("event_publish_gave_up", subject=subject, event_id=msg["event_id"])
        return None

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:
                pass
