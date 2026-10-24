"""Потребитель JetStream: churn.scores.updated → критические события → подписчики и журнал."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import asyncpg
import nats
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from nats.js.errors import NotFoundError
from svckit.bus import ensure_stream
from svckit.logs import get_logger

from . import repository as repo
from .config import Config
from .events import SUBJECT_SCORES, InvalidEvent, ScoresEvent, coalesce, parse_event, unique_new
from .fanout import Fanout
from .metrics import DEAD_LETTERS, ERRORS, EVENTS, LAST_EVENT, RETRIES, SENT
from .rules import JournalEntry

log = get_logger("alerts.worker")

MAX_ACK_PENDING = 1_000
FETCH_TIMEOUT_S = 2.0


class Message(Protocol):
    data: bytes
    subject: str

    async def ack(self) -> None: ...
    async def nak(self, delay: float | None = None) -> None: ...
    async def term(self) -> None: ...


# Отправка сообщения в DLQ: True — опубликовано, исходное можно term().
DeadLetter = Callable[[Message, str], Awaitable[bool]]


def consumer_config(durable: str, ack_wait_s: float, max_deliver: int) -> ConsumerConfig:
    # Свой durable: scoring и notifier про него не знают. DeliverPolicy.NEW — история пересчётов
    # до первого запуска не нужна, сервис всё равно сравнивает с текущим состоянием БД.
    return ConsumerConfig(
        name=durable,
        durable_name=durable,
        ack_policy=AckPolicy.EXPLICIT,
        deliver_policy=DeliverPolicy.NEW,
        ack_wait=ack_wait_s,
        max_deliver=max_deliver + 1,  # запас на случай, если публикация в DLQ не удалась
        max_ack_pending=MAX_ACK_PENDING,
        filter_subjects=[SUBJECT_SCORES],
    )


async def ensure_consumer(js: JetStreamContext, stream: str, durable: str, ack_wait_s: float, max_deliver: int) -> None:
    config = consumer_config(durable, ack_wait_s, max_deliver)
    try:
        info = await js.consumer_info(stream, durable)
    except NotFoundError:
        await js.add_consumer(stream, config=config)
        return
    current = info.config
    if (
        abs((current.ack_wait or 0) - ack_wait_s) > 0.001
        or (current.max_deliver or -1) != config.max_deliver
        or set(current.filter_subjects or ([current.filter_subject] if current.filter_subject else []))
        != {SUBJECT_SCORES}
    ):
        log.info("consumer_update", durable=durable)
        await js.add_consumer(stream, config=config)


def deliveries(msg: Message) -> int:
    try:
        return max(1, int(msg.metadata.num_delivered))  # type: ignore[attr-defined]
    except Exception:
        return 1


def retry_delay(backoff: Sequence[float], delivered: int) -> float:
    if not backoff:
        return 0.0
    return backoff[min(max(delivered, 1), len(backoff)) - 1]


def utcnow() -> datetime:
    moment = datetime.now(UTC)
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


@dataclass(slots=True)
class Outcome:
    journal: list[JournalEntry] = field(default_factory=list)
    personal: int = 0
    baseline: bool = False
    duplicates: int = 0
    queued_external: bool = False


class BatchProcessor:
    """Обработка пачки; от NATS зависит только через протокол Message."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        config: Config,
        *,
        on_enqueued: Callable[[], None] = lambda: None,
        dead_letter: DeadLetter | None = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._pool = pool
        self._config = config
        self._on_enqueued = on_enqueued
        self._dead_letter = dead_letter
        self._now = now
        self._fanout = Fanout(config)

    async def handle(self, messages: Sequence[Message]) -> Outcome | None:
        events: list[ScoresEvent] = []
        valid: list[Message] = []
        for msg in messages:
            try:
                events.append(parse_event(msg.data))
            except InvalidEvent as exc:
                log.warning("event_invalid", subject=msg.subject, error=str(exc))
                EVENTS.labels(result="invalid").inc()
                with contextlib.suppress(Exception):
                    await msg.term()
                continue
            valid.append(msg)
        if not events:
            return None
        try:
            outcome = await self._process(events)
        except asyncio.CancelledError:
            for msg in valid:
                with contextlib.suppress(Exception):
                    await msg.nak()
            raise
        except Exception as exc:
            log.exception("batch_failed", size=len(valid))
            ERRORS.labels(stage="batch").inc()
            await self.retry_or_dead_letter(valid, exc)
            return None
        for msg in valid:
            with contextlib.suppress(Exception):
                await msg.ack()
        LAST_EVENT.set_to_current_time()
        if outcome.queued_external:
            self._on_enqueued()
        return outcome

    async def _process(self, events: list[ScoresEvent]) -> Outcome:
        now = self._now()
        cfg = self._config
        outcome = Outcome()
        async with self._pool.acquire() as conn, conn.transaction():
            await repo.lock(conn)
            done = await repo.processed_event_ids(conn, [e.event_id for e in events])
            fresh = unique_new(events, done)
            outcome.duplicates = len(events) - len(fresh)
            EVENTS.labels(result="duplicate").inc(outcome.duplicates)
            EVENTS.labels(result="new").inc(len(fresh))
            if not fresh:
                return outcome
            plan = coalesce(fresh)
            cur = await repo.current_metrics(conn, now)
            prev = await repo.latest_snapshot(conn)
            if cur is not None:
                await repo.write_snapshot(conn, cur, timedelta(seconds=cfg.snapshot_every_s))

            if not await repo.baseline_taken(conn):
                remembered = await repo.take_baseline(conn, now)
                await repo.mark_processed(conn, plan.event_ids)
                log.info("baseline_taken", customers=remembered)
                outcome.baseline = True
                return outcome

            ids = None if plan.full else sorted(plan.customer_ids)
            transitions = await repo.high_transitions(conn, ids, now, timedelta(seconds=cfg.customer_cooldown_s))
            if cur is not None:
                result = await self._fanout.on_scores(
                    conn, now=now, cur=cur, prev=prev, transitions=transitions, event_ids=plan.event_ids
                )
                outcome.journal = result.journal
                outcome.personal = result.personal
                outcome.queued_external = result.queued_external
            await repo.mark_processed(conn, plan.event_ids)

        if outcome.journal:
            SENT.labels(channel="feed").inc()
        if outcome.journal or outcome.personal:
            log.info("events_raised", journal=len(outcome.journal), personal=outcome.personal)
        return outcome

    async def retry_or_dead_letter(self, messages: Sequence[Message], error: BaseException) -> None:
        """Повтор с паузой по backoff; после max_deliver доставок — в DLQ и term()."""
        reason = f"{type(error).__name__}: {error}"
        for msg in messages:
            delivered = deliveries(msg)
            dead_letter = self._dead_letter if delivered >= self._config.max_deliver else None
            if dead_letter is not None and await dead_letter(msg, reason):
                DEAD_LETTERS.inc()
                log.error("event_dead_lettered", subject=msg.subject, deliveries=delivered, error=reason)
                with contextlib.suppress(Exception):
                    await msg.term()
                continue
            RETRIES.inc()
            with contextlib.suppress(Exception):
                await msg.nak(delay=retry_delay(self._config.retry_backoff_s, delivered))


def _header_safe(value: str, limit: int = 512) -> str:
    flat = " ".join(value.split())[:limit]
    return flat.encode("ascii", "backslashreplace").decode("ascii")


class Worker:
    def __init__(self, config: Config, pool: asyncpg.Pool, *, on_enqueued: Callable[[], None] = lambda: None) -> None:
        self.config = config
        self.pool = pool
        self.nc: NATS | None = None
        self.js: JetStreamContext | None = None
        self._stop = asyncio.Event()
        self.subscribed = False
        self.processor = BatchProcessor(pool, config, on_enqueued=on_enqueued, dead_letter=self.dead_letter)

    @property
    def nats_connected(self) -> bool:
        return self.nc is not None and self.nc.is_connected

    async def connect(self) -> None:
        self.nc = await nats.connect(
            self.config.nats_url, name="alerts", max_reconnect_attempts=-1, reconnect_time_wait=2
        )
        self.js = self.nc.jetstream()
        await ensure_stream(self.js, self.config.stream_name)
        await ensure_consumer(
            self.js, self.config.stream_name, self.config.durable_name, self.config.ack_wait_s, self.config.max_deliver
        )

    async def close(self) -> None:
        self._stop.set()
        if self.nc is not None:
            with contextlib.suppress(Exception):
                await self.nc.drain()

    def stop(self) -> None:
        self._stop.set()

    async def dead_letter(self, msg: Message, error: str) -> bool:
        """Исходное тело без изменений, причина — в заголовках (как у scoring)."""
        if self.js is None:
            return False
        meta: Any = getattr(msg, "metadata", None)
        seq = getattr(getattr(meta, "sequence", None), "stream", None)
        headers = {
            "Nats-Msg-Id": f"dlq-alerts-{seq}" if seq is not None else f"dlq-alerts-{uuid.uuid4()}",
            "Churn-Original-Subject": msg.subject,
            "Churn-Deliveries": str(deliveries(msg)),
            "Churn-Error": _header_safe(error),
        }
        if seq is not None:
            headers["Churn-Stream-Seq"] = str(seq)
        for attempt in range(1, self.config.publish_attempts + 1):
            try:
                await self.js.publish(self.config.dlq_subject, msg.data, headers=headers, timeout=5)
                return True
            except Exception as exc:
                log.warning("dlq_publish_retry", attempt=attempt, error=str(exc))
                await asyncio.sleep(0.5 * attempt)
        ERRORS.labels(stage="dlq").inc()
        return False

    async def consume(self) -> None:
        assert self.js is not None
        sub = await self.js.pull_subscribe_bind(self.config.durable_name, stream=self.config.stream_name)
        self.subscribed = True
        try:
            while not self._stop.is_set():
                try:
                    msgs = await sub.fetch(self.config.fetch_batch, timeout=FETCH_TIMEOUT_S)
                except (TimeoutError, nats.errors.TimeoutError):
                    continue
                except Exception:
                    log.exception("fetch_failed")
                    await asyncio.sleep(1)
                    continue
                try:
                    await self.processor.handle(msgs)
                except asyncio.CancelledError:
                    for msg in msgs:
                        with contextlib.suppress(Exception):
                            await msg.nak()
                    raise
                except Exception as exc:
                    log.exception("batch_unhandled", size=len(msgs))
                    ERRORS.labels(stage="consume").inc()
                    await self.processor.retry_or_dead_letter(msgs, exc)
        finally:
            self.subscribed = False
