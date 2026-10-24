"""Потребитель JetStream: churn.scores.updated → правила → лента и очередь доставки."""

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
from .metrics import DEAD_LETTERS, ERRORS, EVENTS, LAST_EVENT, RAISED, RETRIES, SENT, SUPPRESSED
from .rules import Alert, Thresholds, batch_text, evaluate_customers, evaluate_total, webhook_payload

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
    batch_id: str | None = None
    alerts: list[Alert] = field(default_factory=list)
    baseline: bool = False
    duplicates: int = 0


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
        self._thresholds = Thresholds(
            loss_jump_cents=config.loss_jump_cents,
            total_loss_jump_pct=config.total_loss_jump_pct,
            cooldown=timedelta(seconds=config.cooldown_s),
        )

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
        if outcome.alerts:
            self._on_enqueued()
        return outcome

    async def _process(self, events: list[ScoresEvent]) -> Outcome:
        now = self._now()
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
            meta = await repo.load_meta(conn)
            total_now = plan.total_loss
            if total_now is None:
                total_now = await repo.total_loss_from_summary(conn)

            if meta.baseline_at is None:
                remembered = await repo.take_baseline(conn, now)
                if total_now is not None:
                    _, total_state, _ = evaluate_total(total_now, None, self._thresholds, now)
                    await repo.save_total(conn, total_state)
                await repo.mark_processed(conn, plan.event_ids)
                log.info("baseline_taken", customers=remembered)
                outcome.baseline = True
                return outcome

            ids = None if plan.full else sorted(plan.customer_ids)
            await repo.forget_removed(conn, ids)
            rows = await repo.changed_rows(conn, ids)
            decision = evaluate_customers(rows, self._thresholds, now)
            await repo.save_states(conn, decision.states)
            alerts = list(decision.alerts)
            suppressed = dict(decision.suppressed)

            if total_now is not None:
                total_alert, total_state, total_suppressed = evaluate_total(
                    total_now, meta.total, self._thresholds, now
                )
                await repo.save_total(conn, total_state)
                if total_alert is not None:
                    alerts.insert(0, total_alert)
                if total_suppressed:
                    suppressed["total_loss_jump"] = suppressed.get("total_loss_jump", 0) + 1

            if alerts:
                batch_id = str(uuid.uuid4())
                await repo.insert_feed(conn, alerts, batch_id=batch_id, event_id=plan.event_ids[-1], now=now)
                await self._enqueue(conn, alerts, batch_id=batch_id, event_ids=plan.event_ids, now=now)
                outcome.batch_id = batch_id
                outcome.alerts = alerts
            await repo.mark_processed(conn, plan.event_ids)

        for alert in outcome.alerts:
            RAISED.labels(kind=alert.kind).inc()
        for kind, count in suppressed.items():
            SUPPRESSED.labels(kind=kind).inc(count)
        if outcome.alerts:
            SENT.labels(channel="feed").inc()
            log.info("alerts_raised", batch_id=outcome.batch_id, count=len(outcome.alerts), suppressed=suppressed)
        return outcome

    async def _enqueue(
        self, conn: asyncpg.Connection, alerts: list[Alert], *, batch_id: str, event_ids: list[str], now: datetime
    ) -> None:
        cfg = self._config
        channels = cfg.enabled_channels()
        if "webhook" in channels:
            payload = webhook_payload(
                alerts,
                batch_id=batch_id,
                event_ids=event_ids,
                created_at=now,
                public_url=cfg.public_url,
                max_items=cfg.max_items_in_message,
            )
            await repo.enqueue_delivery(conn, batch_id=batch_id, channel="webhook", payload=payload)
        if "telegram" in channels:
            text = batch_text(alerts, cfg.public_url, cfg.max_items_in_message)
            await repo.enqueue_delivery(conn, batch_id=batch_id, channel="telegram", payload={"text": text})

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
