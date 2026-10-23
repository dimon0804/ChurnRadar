"""Публикация событий в NATS JetStream. Без брокера сервис продолжает работать."""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

from .timeutil import iso, utcnow

log = logging.getLogger("mock-core.events")

STREAM_NAME = "CHURN"
STREAM_SUBJECTS = ["churn.>"]
SUBJECTS = {
    "source.changed": "churn.source.changed",
    "settings.changed": "churn.settings.changed",
    "scores.updated": "churn.scores.updated",
}


def envelope(event_type: str, payload: Dict[str, Any], producer: str) -> Dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "type": event_type,
        "occurred_at": iso(utcnow()),
        "schema_version": 1,
        "producer": producer,
        "payload": payload,
    }


class EventBus:
    def __init__(self, url: str, producer: str, enabled: bool = True):
        self.url = url
        self.producer = producer
        self.enabled = enabled
        self.nc = None
        self.js = None
        self.published = 0
        self.failed = 0
        self._connector: Optional[asyncio.Task] = None
        self._sender: Optional[asyncio.Task] = None
        self._queue: Optional[asyncio.Queue] = None
        self._stopping = False

    @property
    def connected(self) -> bool:
        return self.nc is not None and self.nc.is_connected

    def start(self) -> None:
        if self.enabled and self._connector is None:
            # одна очередь и один отправитель сохраняют порядок событий
            self._queue = asyncio.Queue(maxsize=1000)
            self._connector = asyncio.create_task(self._connect_loop())
            self._sender = asyncio.create_task(self._send_loop())

    async def _connect_loop(self) -> None:
        import nats

        delay = 1.0
        while not self._stopping:
            try:
                self.nc = await nats.connect(
                    self.url,
                    name=self.producer,
                    allow_reconnect=True,
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=2,
                    connect_timeout=3,
                    error_cb=self._on_error,
                    disconnected_cb=self._on_disconnected,
                    reconnected_cb=self._on_reconnected,
                )
                self.js = self.nc.jetstream()
                await self._ensure_stream()
                log.info("подключён к NATS %s", self.url)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - брокер может быть недоступен по любой причине
                log.warning("NATS недоступен (%s), повтор через %.0f с", exc, delay)
                if self.nc is not None:
                    try:
                        await self.nc.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self.nc = None
                    self.js = None
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _on_error(self, exc: Exception) -> None:
        log.warning("NATS: %s", str(exc) or type(exc).__name__)

    async def _on_disconnected(self) -> None:
        log.warning("соединение с NATS потеряно, переподключаюсь")

    async def _on_reconnected(self) -> None:
        log.info("соединение с NATS восстановлено")
        try:
            await self._ensure_stream()
        except Exception as exc:  # noqa: BLE001
            log.warning("не удалось проверить стрим %s: %s", STREAM_NAME, exc)

    async def _ensure_stream(self) -> None:
        from nats.js.api import RetentionPolicy, StorageType, StreamConfig
        from nats.js.errors import BadRequestError, NotFoundError

        try:
            await self.js.stream_info(STREAM_NAME)
            return
        except NotFoundError:
            pass
        config = StreamConfig(
            name=STREAM_NAME,
            subjects=STREAM_SUBJECTS,
            storage=StorageType.FILE,
            retention=RetentionPolicy.LIMITS,
            max_age=24 * 3600,
        )
        try:
            await self.js.add_stream(config)
            log.info("создан стрим %s", STREAM_NAME)
        except BadRequestError as exc:
            # стрим мог создать соседний сервис между проверкой и созданием
            log.info("стрим %s уже существует: %s", STREAM_NAME, exc)

    def publish(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Отправляет событие в фоне и сразу возвращает конверт."""
        env = envelope(event_type, payload, self.producer)
        if self._queue is None:
            return env
        try:
            self._queue.put_nowait((SUBJECTS[event_type], env))
        except asyncio.QueueFull:
            self.failed += 1
            log.warning("очередь событий переполнена, %s пропущено", event_type)
        return env

    async def _send_loop(self) -> None:
        while True:
            subject, env = await self._queue.get()
            try:
                await self._send(subject, env)
            finally:
                self._queue.task_done()

    async def _send(self, subject: str, env: Dict[str, Any]) -> None:
        if not self.connected or self.js is None:
            self.failed += 1
            log.debug("NATS не подключён, событие %s пропущено", env["type"])
            return
        data = json.dumps(env, ensure_ascii=False).encode("utf-8")
        try:
            await self.js.publish(subject, data, timeout=3, headers={"Nats-Msg-Id": env["event_id"]})
            self.published += 1
        except Exception as exc:  # noqa: BLE001
            self.failed += 1
            log.warning("не удалось опубликовать %s: %s", subject, exc)

    async def stop(self) -> None:
        self._stopping = True
        if self._queue is not None and self.connected:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=2)
            except asyncio.TimeoutError:
                pass
        for task in (self._connector, self._sender):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self.nc is not None:
            try:
                await self.nc.drain()
            except Exception:  # noqa: BLE001
                pass
