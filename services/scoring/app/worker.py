"""Потребитель JetStream: события → план → пересчёт → churn.scores.updated."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Sequence
from typing import Protocol

import asyncpg
import nats
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import BadRequestError, NotFoundError

from . import repository as repo
from .config import Config
from .engine import RunResult, Scorer
from .events import (
    SUBJECT_SCORES,
    SUBJECT_SETTINGS,
    SUBJECT_SOURCE,
    Event,
    InvalidEvent,
    coalesce,
    make_envelope,
    parse_event,
    scores_updated_payload,
    unique_new,
)
from .logs import get_logger
from .metrics import EVENTS, LAST_RUN

log = get_logger("scoring.worker")


class Message(Protocol):
    data: bytes
    subject: str

    async def ack(self) -> None: ...
    async def nak(self, delay: float | None = None) -> None: ...
    async def term(self) -> None: ...
    async def in_progress(self) -> None: ...


class Publisher(Protocol):
    async def __call__(self, result: RunResult) -> None: ...


async def ensure_stream(js: JetStreamContext, name: str) -> None:
    try:
        await js.stream_info(name)
        return
    except NotFoundError:
        pass
    config = StreamConfig(
        name=name,
        subjects=["churn.>"],
        storage=StorageType.FILE,
        retention=RetentionPolicy.LIMITS,
        max_age=24 * 3600,
    )
    try:
        await js.add_stream(config)
    except BadRequestError:
        # Стрим успела создать другая служба.
        await js.stream_info(name)


async def ensure_consumer(js: JetStreamContext, stream: str, durable: str, ack_wait_s: float) -> None:
    config = ConsumerConfig(
        name=durable,
        durable_name=durable,
        ack_policy=AckPolicy.EXPLICIT,
        deliver_policy=DeliverPolicy.ALL,
        ack_wait=ack_wait_s,
        max_ack_pending=10_000,
        filter_subjects=[SUBJECT_SOURCE, SUBJECT_SETTINGS],
    )
    try:
        await js.consumer_info(stream, durable)
    except NotFoundError:
        await js.add_consumer(stream, config=config)


class BatchProcessor:
    """Обработка пачки сообщений; от NATS зависит только через протокол Message."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        scorer: Scorer,
        publish: Publisher,
        *,
        incremental_max: int,
    ) -> None:
        self._pool = pool
        self._scorer = scorer
        self._publish = publish
        self._incremental_max = incremental_max

    async def handle(self, messages: Sequence[Message]) -> RunResult | None:
        events: list[Event] = []
        valid: list[Message] = []
        for msg in messages:
            try:
                event = parse_event(msg.data, msg.subject)
            except InvalidEvent as exc:
                log.warning("event_invalid", subject=msg.subject, error=str(exc))
                EVENTS.labels(type="unknown", result="invalid").inc()
                await msg.term()
                continue
            events.append(event)
            valid.append(msg)
        if not events:
            return None

        async with self._pool.acquire() as conn:
            done = await repo.processed_event_ids(conn, [e.event_id for e in events])
        fresh = unique_new(events, done)
        fresh_ids = {e.event_id for e in fresh}
        for event in events:
            EVENTS.labels(type=event.type, result="new" if event.event_id in fresh_ids else "duplicate").inc()
        plan = coalesce(fresh, self._incremental_max)

        if plan.is_empty:
            if plan.event_ids:
                async with self._pool.acquire() as conn:
                    await repo.mark_processed(conn, plan.event_ids)
            await _ack_all(valid)
            return None

        keepalive = asyncio.create_task(_keep_in_progress(valid))
        try:
            result = await self._scorer.run(
                full=plan.full, account_ids=sorted(plan.account_ids), event_ids=plan.event_ids
            )
        except Exception:
            log.exception("rescore_failed", full=plan.full, accounts=len(plan.account_ids))
            keepalive.cancel()
            for msg in valid:
                with contextlib.suppress(Exception):
                    await msg.nak(delay=5)
            return None
        finally:
            keepalive.cancel()
        await _ack_all(valid)
        if result is not None:
            LAST_RUN.set_to_current_time()
            await self._publish(result)
        return result


async def _ack_all(messages: Sequence[Message]) -> None:
    for msg in messages:
        with contextlib.suppress(Exception):
            await msg.ack()


async def _keep_in_progress(messages: Sequence[Message], every_s: float = 20.0) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        while True:
            await asyncio.sleep(every_s)
            for msg in messages:
                with contextlib.suppress(Exception):
                    await msg.in_progress()


class Worker:
    def __init__(self, config: Config, pool: asyncpg.Pool, scorer: Scorer) -> None:
        self.config = config
        self.pool = pool
        self.scorer = scorer
        self.nc: NATS | None = None
        self.js: JetStreamContext | None = None
        self._stop = asyncio.Event()
        self.processor = BatchProcessor(
            pool, scorer, self.publish, incremental_max=config.incremental_max_accounts
        )

    @property
    def nats_connected(self) -> bool:
        return self.nc is not None and self.nc.is_connected

    async def connect(self) -> None:
        self.nc = await nats.connect(
            self.config.nats_url,
            name="scoring",
            max_reconnect_attempts=-1,
            reconnect_time_wait=2,
        )
        self.js = self.nc.jetstream()
        await ensure_stream(self.js, self.config.stream_name)
        await ensure_consumer(self.js, self.config.stream_name, self.config.durable_name, self.config.ack_wait_s)

    async def close(self) -> None:
        self._stop.set()
        if self.nc is not None:
            with contextlib.suppress(Exception):
                await self.nc.drain()

    async def publish(self, result: RunResult) -> None:
        if self.js is None:
            return
        payload = scores_updated_payload(
            model_version=result.model_version,
            scored_at=result.scored_at,
            changed_customer_ids=result.changed_ids,
            full=result.full,
            duration_ms=result.duration_ms,
            summary=result.summary,
        )
        envelope = make_envelope("scores.updated", payload)
        try:
            await self.js.publish(
                SUBJECT_SCORES,
                json.dumps(envelope, ensure_ascii=False).encode(),
                headers={"Nats-Msg-Id": envelope["event_id"]},
            )
        except Exception:
            log.exception("publish_failed", subject=SUBJECT_SCORES)

    async def consume(self) -> None:
        assert self.js is not None
        sub = await self.js.pull_subscribe_bind(self.config.durable_name, stream=self.config.stream_name)
        debounce = self.config.debounce_ms / 1000
        while not self._stop.is_set():
            try:
                msgs = await sub.fetch(self.config.fetch_batch, timeout=2)
            except (TimeoutError, nats.errors.TimeoutError):
                continue
            except Exception:
                log.exception("fetch_failed")
                await asyncio.sleep(1)
                continue
            # Коалесинг: даём подтянуться остальной пачке и пересчитываем один раз.
            if debounce > 0:
                await asyncio.sleep(debounce)
            while len(msgs) < self.config.fetch_batch * 8:
                try:
                    more = await sub.fetch(self.config.fetch_batch, timeout=0.05)
                except (TimeoutError, nats.errors.TimeoutError):
                    break
                if not more:
                    break
                msgs.extend(more)
            log.debug("batch_received", size=len(msgs))
            await self.processor.handle(msgs)

    async def periodic(self) -> None:
        """Пересчёт по времени, если опорное время не зафиксировано снимком (as_of = now()).

        Пауза между пересчётами — не меньше RESCORE_INTERVAL_S и не меньше двух длительностей
        последнего полного пересчёта, чтобы на больших объёмах воркер не считал без остановки.
        """
        if self.config.rescore_interval_s <= 0:
            return
        while not self._stop.is_set():
            pause = max(self.config.rescore_interval_s, 2 * self.scorer.last_full_seconds)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=pause)
                return
            except TimeoutError:
                pass
            try:
                async with self.pool.acquire() as conn:
                    frozen = await repo.fetch_clock(conn) is not None
                if frozen or self.scorer.busy:
                    continue
                result = await self.scorer.run(full=True, wait_lock=False)
                if result is not None:
                    LAST_RUN.set_to_current_time()
                    await self.publish(result)
            except Exception:
                log.exception("periodic_rescore_failed")

    async def initial(self) -> None:
        async with self.pool.acquire() as conn:
            empty = await repo.scores_empty(conn)
        if not empty:
            return
        started = time.perf_counter()
        result = await self.scorer.run(full=True)
        if result is not None:
            LAST_RUN.set_to_current_time()
            log.info("initial_rescore", duration_ms=round((time.perf_counter() - started) * 1000, 1))
            await self.publish(result)

    def stop(self) -> None:
        self._stop.set()
