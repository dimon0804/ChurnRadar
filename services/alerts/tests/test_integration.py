"""Интеграция с реальными Postgres и NATS: событие в шину → лента и webhook.

scoring здесь не запускается: его результат (scoring.customer_scores) пишем сами,
а churn.scores.updated публикуем в том же формате, что и он.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any

import nats
import pytest

from app.channels import build_channels
from app.config import Config
from app.delivery import Dispatcher
from app.events import SUBJECT_SCORES
from app.metrics import EVENTS, FAILED, SENT
from app.worker import Worker
from conftest import TEST_DATABASE_URL, TEST_NATS_URL, WebhookServer, put_score

pytestmark = pytest.mark.integration


def scores_updated(ids: list[int], total_loss: int, *, full: bool = False, event_id: str | None = None) -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "type": "scores.updated",
        "occurred_at": "2026-10-19T15:00:00.000Z",
        "schema_version": 1,
        "producer": "scoring",
        "payload": {
            "model_version": 1,
            "scored_at": "2026-10-19T15:00:00.000Z",
            "changed_customer_ids": [] if full else ids,
            "full": full,
            "duration_ms": 5.0,
            "summary": {"customers_total": 3, "by_level": {}, "expected_loss": total_loss, "exposure": 0},
        },
    }


async def wait_for(check: Callable[[], Any], timeout: float = 15.0, what: str = "условие") -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        result = await check()
        if result:
            return result
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"не дождались: {what}")
        await asyncio.sleep(0.1)


class Stack:
    """Сервис оповещений целиком (потребитель + доставка) поверх тестовых Postgres и NATS."""

    def __init__(self, pool: Any, config: Config) -> None:
        import httpx

        self.pool = pool
        self.config = config
        self.client = httpx.AsyncClient()
        self.dispatcher = Dispatcher(
            pool,
            build_channels(config, self.client),
            interval_s=0.1,
            backoff_s=config.delivery_backoff_s,
            max_attempts=config.delivery_max_attempts,
            lease_s=5,
        )
        self.worker = Worker(config, pool, on_enqueued=self.dispatcher.kick)
        self.tasks: list[asyncio.Task[Any]] = []
        self.nc: Any = None

    async def start(self) -> None:
        try:
            await asyncio.wait_for(self.worker.connect(), timeout=5)
        except Exception as exc:
            pytest.skip(f"NATS недоступен: {exc}")
        self.tasks = [asyncio.create_task(self.worker.consume()), asyncio.create_task(self.dispatcher.run())]
        await wait_for(lambda: _true(self.worker.subscribed), what="подписка")
        self.nc = await nats.connect(TEST_NATS_URL)

    async def publish(self, envelope: dict, *, msg_id: str | None = None) -> None:
        js = self.nc.jetstream()
        headers = {"Nats-Msg-Id": msg_id} if msg_id else None
        await js.publish(SUBJECT_SCORES, json.dumps(envelope).encode(), headers=headers)

    async def processed(self, event_id: str) -> bool:
        async with self.pool.acquire() as conn:
            return bool(
                await conn.fetchval("SELECT count(*) FROM alerts.processed_events WHERE event_id = $1::uuid", event_id)
            )

    async def feed(self) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            return [dict(r) for r in await conn.fetch("SELECT * FROM alerts.feed ORDER BY id")]

    async def stop(self) -> None:
        self.worker.stop()
        self.dispatcher.stop()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.worker.close()
        if self.nc is not None:
            with contextlib.suppress(Exception):
                await self.nc.close()
        await self.client.aclose()


async def _true(value: Any) -> Any:
    return value


def make_config(**overrides: Any) -> Config:
    base = Config(
        database_url=TEST_DATABASE_URL,
        nats_url=TEST_NATS_URL,
        durable_name=f"alerts-test-{uuid.uuid4().hex[:8]}",  # свой потребитель на каждый тест
        public_url="https://churn.example",
        loss_jump_cents=5_000,
        total_loss_jump_pct=10.0,
        cooldown_s=3600,
        delivery_backoff_s=(0.2, 0.2, 0.2),
        delivery_max_attempts=20,
        retry_backoff_s=(0.2,),
    )
    return replace(base, **overrides)


@pytest.fixture
async def seeded(db_pool: Any) -> AsyncIterator[Any]:
    async with db_pool.acquire() as conn:
        await put_score(conn, 1, "medium", 1_000)
        await put_score(conn, 2, "low", 500)
        await put_score(conn, 3, "high", 20_000)
    yield db_pool


async def test_event_to_feed_and_webhook_without_duplicates(seeded: Any, webhook: WebhookServer) -> None:
    stack = Stack(seeded, make_config(webhook_url=webhook.url + "/hook", webhook_secret="s"))
    await stack.start()
    try:
        # первый пересчёт — исходный срез, оповещений нет, хотя клиент 3 уже в высоком риске
        first = scores_updated([], 21_500, full=True)
        await stack.publish(first)
        await wait_for(lambda: stack.processed(first["event_id"]), what="исходный срез")
        assert await stack.feed() == []

        # scoring пересчитал клиента 1: перешёл в высокий риск
        async with seeded.acquire() as conn:
            await put_score(conn, 1, "high", 9_000)
        event = scores_updated([1], 22_000)
        await stack.publish(event, msg_id=event["event_id"])
        feed = await wait_for(stack.feed, what="запись в ленте")
        assert len(feed) == 1
        assert feed[0]["kind"] == "high_risk" and feed[0]["customer_id"] == 1
        assert "перешёл в высокий риск" in feed[0]["message"]

        await wait_for(lambda: _true(len(webhook.requests) == 1), what="webhook")
        hook = webhook.requests[0]
        assert hook["path"] == "/hook"
        assert hook["headers"]["X-ChurnRadar-Signature"].startswith("sha256=")
        assert hook["body"]["alerts"][0]["url"] == "https://churn.example/customers/1"
        assert hook["body"]["text"].startswith("ChurnRadar: 1 оповещение")

        # то же событие ещё раз (без Nats-Msg-Id, чтобы JetStream не отсёк сам): дубля нет
        duplicates_before = EVENTS.labels(result="duplicate")._value.get()
        await stack.publish(event)
        await wait_for(lambda: _true(EVENTS.labels(result="duplicate")._value.get() > duplicates_before), what="повтор")
        await asyncio.sleep(0.5)
        assert len(await stack.feed()) == 1
        assert len(webhook.requests) == 1
        async with seeded.acquire() as conn:
            assert await conn.fetchval("SELECT status FROM alerts.deliveries") == "sent"
    finally:
        await stack.stop()


async def test_loss_and_total_jump_in_one_batch(seeded: Any) -> None:
    stack = Stack(seeded, make_config())
    await stack.start()
    try:
        first = scores_updated([], 21_500, full=True)
        await stack.publish(first)
        await wait_for(lambda: stack.processed(first["event_id"]), what="исходный срез")

        async with seeded.acquire() as conn:
            await put_score(conn, 2, "low", 7_000)  # +€65 при пороге €50
        event = scores_updated([], 28_000, full=True)  # общая +30 %
        await stack.publish(event)
        feed = await wait_for(stack.feed, what="лента")
        kinds = sorted(r["kind"] for r in feed)
        assert kinds == ["loss_jump", "total_loss_jump"]
        assert len({r["batch_id"] for r in feed}) == 1  # одна пачка
        # каналы не настроены — в очереди доставки пусто, лента есть всегда
        async with seeded.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM alerts.deliveries") == 0
    finally:
        await stack.stop()


async def test_webhook_down_retries_then_recovers(seeded: Any, webhook: WebhookServer) -> None:
    webhook.status = 503
    stack = Stack(seeded, make_config(webhook_url=webhook.url))
    await stack.start()
    failed_before = FAILED.labels(channel="webhook")._value.get()
    sent_before = SENT.labels(channel="webhook")._value.get()
    try:
        first = scores_updated([], 21_500, full=True)
        await stack.publish(first)
        await wait_for(lambda: stack.processed(first["event_id"]), what="исходный срез")
        async with seeded.acquire() as conn:
            await put_score(conn, 2, "high", 600)
        await stack.publish(scores_updated([2], 21_600))

        await wait_for(lambda: _true(len(webhook.requests) >= 3), what="повторные попытки")
        assert FAILED.labels(channel="webhook")._value.get() - failed_before >= 3
        async with seeded.acquire() as conn:
            row = await conn.fetchrow("SELECT status, attempts, last_error FROM alerts.deliveries")
        assert row["status"] == "pending" and row["attempts"] >= 2 and "503" in row["last_error"]
        assert len(await stack.feed()) == 1  # лента не зависит от внешнего канала

        webhook.status = 200
        await wait_for(lambda: _true(SENT.labels(channel="webhook")._value.get() > sent_before), what="доставка")
        async with seeded.acquire() as conn:
            assert await conn.fetchval("SELECT status FROM alerts.deliveries") == "sent"
    finally:
        await stack.stop()


async def test_webhook_unreachable_counts_failures(seeded: Any) -> None:
    from svckit.testing import free_port

    stack = Stack(
        seeded,
        make_config(webhook_url=f"http://127.0.0.1:{free_port()}/nobody", webhook_timeout_s=1, delivery_max_attempts=2),
    )
    await stack.start()
    failed_before = FAILED.labels(channel="webhook")._value.get()
    try:
        first = scores_updated([], 21_500, full=True)
        await stack.publish(first)
        await wait_for(lambda: stack.processed(first["event_id"]), what="исходный срез")
        async with seeded.acquire() as conn:
            await put_score(conn, 1, "high", 2_000)
        await stack.publish(scores_updated([1], 21_600))

        async def given_up() -> bool:
            async with seeded.acquire() as conn:
                return await conn.fetchval("SELECT status FROM alerts.deliveries") == "failed"

        await wait_for(given_up, what="отказ после всех попыток")
        assert FAILED.labels(channel="webhook")._value.get() - failed_before == 2
    finally:
        await stack.stop()


async def _baseline(stack: Stack) -> None:
    first = scores_updated([], 21_500, full=True)
    await stack.publish(first)
    await wait_for(lambda: stack.processed(first["event_id"]), what="исходный срез")


async def test_hundred_transitions_fold_into_one_record_and_one_webhook(seeded: Any, webhook: WebhookServer) -> None:
    async with seeded.acquire() as conn:
        for cid in range(100, 200):
            await put_score(conn, cid, "medium", 1_000)
    stack = Stack(seeded, make_config(webhook_url=webhook.url, bulk_threshold=20))
    await stack.start()
    try:
        await _baseline(stack)
        # пересчёт после смены модели: сотня клиентов разом ушла в высокий риск
        async with seeded.acquire() as conn:
            for cid in range(100, 200):
                await put_score(conn, cid, "high", 1_000 + cid * 10)
        event = scores_updated(list(range(100, 200)), 400_000)
        await stack.publish(event)
        await wait_for(lambda: stack.processed(event["event_id"]), what="пачка")

        feed = await stack.feed()
        assert [r["kind"] for r in feed] == ["bulk"]
        assert feed[0]["customer_id"] is None
        assert "в высоком риске 101 клиент (было 1)" in feed[0]["message"]
        assert "Самые дорогие: #199 (€29,90), #198 (€29,80), #197 (€29,70)." in feed[0]["message"]

        await wait_for(lambda: _true(len(webhook.requests) == 1), what="webhook")
        body = webhook.requests[0]["body"]
        assert [a["kind"] for a in body["alerts"]] == ["bulk"]
        assert body["alerts"][0]["url"] == "https://churn.example/customers?level=high&sort=expected_loss&order=desc"
        async with seeded.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM alerts.deliveries") == 1
            # состояние перебазировано: все сто запомнены как высокий риск
            assert await conn.fetchval("SELECT count(*) FROM alerts.customer_state WHERE risk_level = 'high'") == 101

        # следующий пересчёт без изменений — ни отложенных оповещений, ни новой доставки
        again = scores_updated([], 400_000, full=True)
        await stack.publish(again)
        await wait_for(lambda: stack.processed(again["event_id"]), what="повторный пересчёт")
        await asyncio.sleep(0.3)
        assert len(await stack.feed()) == 1
        assert len(webhook.requests) == 1
    finally:
        await stack.stop()


async def test_snapshot_new_customers_only_in_summary(seeded: Any) -> None:
    stack = Stack(seeded, make_config())
    await stack.start()
    try:
        await _baseline(stack)
        # новый снимок: три новых клиента сразу в высоком риске, порог (20) не превышен
        async with seeded.acquire() as conn:
            for cid in (501, 502, 503):
                await put_score(conn, cid, "high", 5_000)
        event = scores_updated([], 36_500, full=True)
        await stack.publish(event)
        await wait_for(lambda: stack.processed(event["event_id"]), what="снимок")
        feed = await stack.feed()
        assert [r["kind"] for r in feed] == ["bulk"]
        assert feed[0]["message"].startswith("Загружены новые данные: в высоком риске 4 клиента (было 1)")
    finally:
        await stack.stop()


async def test_single_new_customer_from_stream_is_individual(seeded: Any) -> None:
    stack = Stack(seeded, make_config())
    await stack.start()
    try:
        await _baseline(stack)
        async with seeded.acquire() as conn:
            await put_score(conn, 777, "high", 5_000)
        event = scores_updated([777], 22_000)
        await stack.publish(event)
        await wait_for(lambda: stack.processed(event["event_id"]), what="событие")
        feed = await stack.feed()
        assert [(r["kind"], r["customer_id"]) for r in feed] == [("high_risk", 777)]
        assert feed[0]["message"].startswith("Новый клиент #777 (user777@example.com) сразу в высоком риске")
    finally:
        await stack.stop()
