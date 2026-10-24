"""Потребитель JetStream: события → план → пересчёт → churn.scores.updated."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import asyncpg
import nats
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from nats.js.errors import NotFoundError
from svckit.bus import ensure_stream
from svckit.logs import get_logger
from svckit.outbox import OutboxRelay

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
from .metrics import DEAD_LETTERS, ERRORS, EVENTS, LAST_RUN, RETRIES

log = get_logger("scoring.worker")

DEFAULT_BACKOFF_S = (1.0, 5.0, 30.0, 120.0)
MAX_ACK_PENDING = 10_000
FETCH_TIMEOUT_S = 2.0


class Message(Protocol):
    data: bytes
    subject: str

    async def ack(self) -> None: ...
    async def nak(self, delay: float | None = None) -> None: ...
    async def term(self) -> None: ...
    async def in_progress(self) -> None: ...


class Publisher(Protocol):
    async def __call__(self, result: RunResult) -> None: ...


# Отправка сообщения в DLQ: True — опубликовано, исходное можно term().
DeadLetter = Callable[[Message, str], Awaitable[bool]]


def consumer_config(durable: str, ack_wait_s: float, max_deliver: int) -> ConsumerConfig:
    # backoff на стороне сервера не задаём: он подменяет ack_wait на backoff[0], и долгий полный
    # пересчёт (keepalive раз в 20 с) получал бы повторные доставки. Паузы даём через nak(delay).
    # max_deliver на одну доставку больше нашего порога: запас, если публикация в DLQ не удалась.
    return ConsumerConfig(
        name=durable,
        durable_name=durable,
        ack_policy=AckPolicy.EXPLICIT,
        deliver_policy=DeliverPolicy.ALL,
        ack_wait=ack_wait_s,
        max_deliver=max_deliver + 1,
        max_ack_pending=MAX_ACK_PENDING,
        filter_subjects=[SUBJECT_SOURCE, SUBJECT_SETTINGS],
    )


def _consumer_drift(current: ConsumerConfig, wanted: ConsumerConfig) -> list[str]:
    """Поля, которые можно обновить на живом потребителе и которые разошлись с нужными."""
    drift = []
    if abs((current.ack_wait or 0) - (wanted.ack_wait or 0)) > 0.001:
        drift.append("ack_wait")
    if (current.max_deliver or -1) != wanted.max_deliver:
        drift.append("max_deliver")
    if (current.max_ack_pending or 0) != wanted.max_ack_pending:
        drift.append("max_ack_pending")
    if current.backoff:
        drift.append("backoff")
    filters = set(current.filter_subjects or ([current.filter_subject] if current.filter_subject else []))
    if filters != set(wanted.filter_subjects or []):
        drift.append("filter_subjects")
    return drift


async def ensure_consumer(
    js: JetStreamContext, stream: str, durable: str, ack_wait_s: float, max_deliver: int = 10
) -> None:
    config = consumer_config(durable, ack_wait_s, max_deliver)
    try:
        info = await js.consumer_info(stream, durable)
    except NotFoundError:
        await js.add_consumer(stream, config=config)
        return
    drift = _consumer_drift(info.config, config)
    if drift:
        # CONSUMER.CREATE без action на существующем потребителе — обновление (nats-server 2.10+)
        log.info("consumer_update", durable=durable, fields=drift)
        await js.add_consumer(stream, config=config)


def deliveries(msg: Message) -> int:
    """Номер доставки из метаданных JetStream; у сообщений без них считаем первой."""
    try:
        return max(1, int(msg.metadata.num_delivered))  # type: ignore[attr-defined]
    except Exception:
        return 1


def retry_delay(backoff: Sequence[float], delivered: int) -> float:
    if not backoff:
        return 0.0
    return backoff[min(max(delivered, 1), len(backoff)) - 1]


async def _ack_all(messages: Sequence[Message]) -> None:
    for msg in messages:
        with contextlib.suppress(Exception):
            await msg.ack()


async def _nak_on_stop(messages: Sequence[Message]) -> None:
    """Вернуть сообщения сразу, не дожидаясь ack_wait: их подхватит другая реплика."""
    for msg in messages:
        with contextlib.suppress(Exception):
            await msg.nak()


async def _keep_in_progress(messages: Sequence[Message], every_s: float = 20.0) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        while True:
            await asyncio.sleep(every_s)
            for msg in messages:
                with contextlib.suppress(Exception):
                    await msg.in_progress()


class BatchProcessor:
    """Обработка пачки сообщений; от NATS зависит только через протокол Message."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        scorer: Scorer,
        publish: Publisher,
        *,
        incremental_max: int,
        dead_letter: DeadLetter | None = None,
        max_deliver: int = 10,
        backoff: Sequence[float] = DEFAULT_BACKOFF_S,
    ) -> None:
        self._pool = pool
        self._scorer = scorer
        self._publish = publish
        self._incremental_max = incremental_max
        self._dead_letter = dead_letter
        self._max_deliver = max(1, max_deliver)
        self._backoff = tuple(backoff)

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
        try:
            return await self._process(events, valid)
        except asyncio.CancelledError:
            # остановка посреди пачки
            await _nak_on_stop(valid)
            raise
        except Exception as exc:
            # обрыв БД на проверке/отметке обработанных и прочее вне пересчёта
            log.exception("batch_failed", size=len(valid))
            ERRORS.labels(stage="batch").inc()
            await self.retry_or_dead_letter(valid, exc)
            return None

    async def _process(self, events: list[Event], valid: list[Message]) -> RunResult | None:
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
        except Exception as exc:
            log.exception("rescore_failed", full=plan.full, accounts=len(plan.account_ids))
            ERRORS.labels(stage="rescore").inc()
            keepalive.cancel()
            await self.retry_or_dead_letter(valid, exc)
            return None
        finally:
            keepalive.cancel()
        await _ack_all(valid)
        if result is not None:
            LAST_RUN.set_to_current_time()
            await self._publish(result)
        return result

    async def retry_or_dead_letter(self, messages: Sequence[Message], error: BaseException) -> None:
        """Повтор с паузой по backoff; после max_deliver доставок — в DLQ и term()."""
        reason = f"{type(error).__name__}: {error}"
        for msg in messages:
            delivered = deliveries(msg)
            dead_letter = self._dead_letter if delivered >= self._max_deliver else None
            # если DLQ недоступна — обычный nak: у потребителя max_deliver с запасом на ещё одну попытку
            if dead_letter is not None and await dead_letter(msg, reason):
                DEAD_LETTERS.labels(subject=msg.subject).inc()
                log.error("event_dead_lettered", subject=msg.subject, deliveries=delivered, error=reason)
                with contextlib.suppress(Exception):
                    await msg.term()
                continue
            RETRIES.inc()
            with contextlib.suppress(Exception):
                await msg.nak(delay=retry_delay(self._backoff, delivered))


def _header_safe(value: str, limit: int = 512) -> str:
    """Заголовки NATS — одна строка ASCII."""
    flat = " ".join(value.split())[:limit]
    return flat.encode("ascii", "backslashreplace").decode("ascii")


class Worker:
    def __init__(self, config: Config, pool: asyncpg.Pool, scorer: Scorer) -> None:
        self.config = config
        self.pool = pool
        self.scorer = scorer
        self.nc: NATS | None = None
        self.js: JetStreamContext | None = None
        # relay scoring.scores_outbox; без него (тесты) событие уходит напрямую
        self.relay: OutboxRelay | None = None
        self._stop = asyncio.Event()
        self.subscribed = False
        self.processor = BatchProcessor(
            pool,
            scorer,
            self.publish,
            incremental_max=config.incremental_max_accounts,
            dead_letter=self.dead_letter,
            max_deliver=config.max_deliver,
            backoff=config.retry_backoff_s,
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
        await ensure_consumer(
            self.js,
            self.config.stream_name,
            self.config.durable_name,
            self.config.ack_wait_s,
            self.config.max_deliver,
        )

    async def close(self) -> None:
        self._stop.set()
        if self.nc is not None:
            with contextlib.suppress(Exception):
                await self.nc.drain()

    async def _publish_with_retry(self, subject: str, data: bytes, headers: dict[str, str], stage: str) -> bool:
        """Публикация с повторами; Nats-Msg-Id один на все попытки, дублей в стриме не будет."""
        attempts = max(1, self.config.publish_attempts)
        for attempt in range(1, attempts + 1):
            if self.js is None:
                break
            try:
                await self.js.publish(subject, data, headers=headers, timeout=5)
                return True
            except Exception as exc:
                log.warning("publish_retry", subject=subject, attempt=attempt, error=str(exc))
                if attempt < attempts:
                    await asyncio.sleep(self.config.publish_retry_delay_s * attempt)
        ERRORS.labels(stage=stage).inc()
        log.error("publish_failed", subject=subject, attempts=attempts)
        return False

    async def publish(self, result: RunResult) -> None:
        # событие уже лежит в scoring.scores_outbox (записано в транзакции пересчёта): будим relay
        if self.relay is not None:
            self.relay.kick()
            return
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
        await self._publish_with_retry(
            SUBJECT_SCORES,
            json.dumps(envelope, ensure_ascii=False).encode(),
            {"Nats-Msg-Id": envelope["event_id"]},
            stage="publish",
        )

    async def dead_letter(self, msg: Message, error: str) -> bool:
        """Исходное тело без изменений (можно переиграть в исходный subject), причина — в заголовках."""
        meta: Any = getattr(msg, "metadata", None)
        seq = getattr(getattr(meta, "sequence", None), "stream", None)
        headers = {
            # повтор DLQ того же сообщения не задвоится
            "Nats-Msg-Id": f"dlq-scoring-{seq}" if seq is not None else f"dlq-scoring-{uuid.uuid4()}",
            "Churn-Original-Subject": msg.subject,
            "Churn-Deliveries": str(deliveries(msg)),
            "Churn-Error": _header_safe(error),
        }
        if seq is not None:
            headers["Churn-Stream-Seq"] = str(seq)
        return await self._publish_with_retry(self.config.dlq_subject, msg.data, headers, stage="dlq")

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
                    await self._collect_and_handle(sub, msgs)
                except asyncio.CancelledError:
                    await _nak_on_stop(msgs)
                    raise
                except Exception as exc:
                    # handle сам разбирает ошибки; сюда — только непредвиденное, цикл живёт дальше
                    log.exception("batch_unhandled", size=len(msgs))
                    ERRORS.labels(stage="consume").inc()
                    await self.processor.retry_or_dead_letter(msgs, exc)
        finally:
            self.subscribed = False

    async def _collect_and_handle(self, sub: Any, msgs: list[Any]) -> None:
        # Коалесинг: даём подтянуться остальной пачке и пересчитываем один раз.
        debounce = self.config.debounce_ms / 1000
        if debounce > 0:
            await asyncio.sleep(debounce)
        while len(msgs) < self.config.fetch_batch * 8:
            try:
                more = await sub.fetch(self.config.fetch_batch, timeout=0.05)
            except (TimeoutError, nats.errors.TimeoutError):
                break
            except Exception as exc:
                log.warning("fetch_more_failed", error=str(exc))
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
                ERRORS.labels(stage="periodic").inc()

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
