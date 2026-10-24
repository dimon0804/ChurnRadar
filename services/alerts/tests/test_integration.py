"""Интеграция с реальными Postgres и NATS: событие в шину → подписчики, журнал, почта, Telegram, webhook.

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
from svckit.mail import MailConfig

from app import repository as repo
from app.catalog import parse_prefs
from app.channels import build_channels
from app.config import Config
from app.delivery import Dispatcher
from app.events import SUBJECT_SCORES
from app.metrics import EVENTS, FAILED, SENT
from app.worker import Worker
from conftest import TEST_DATABASE_URL, TEST_NATS_URL, WebhookServer, put_score
from fakes import FakeSmtp, FakeTelegram, put_summary

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
        cooldown_s=3600,
        snapshot_every_s=0,  # в тестах каждая точка истории — отдельный снимок
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
        await put_summary(conn, loss=100_000, high=1, share=0.2)
    yield db_pool


async def _baseline(stack: Stack) -> None:
    first = scores_updated([], 100_000, full=True)
    await stack.publish(first)
    await wait_for(lambda: stack.processed(first["event_id"]), what="исходный срез")


async def _subscribe(pool: Any, username: str, body: dict[str, Any]) -> None:
    async with pool.acquire() as conn:
        await repo.save_prefs(conn, parse_prefs(username, body), username)


async def _notifications(pool: Any, username: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM alerts.notifications WHERE username = $1 ORDER BY id", username)
    return [dict(r) for r in rows]


async def test_personal_thresholds_panel_and_email(seeded: Any, smtp: FakeSmtp) -> None:
    mail = MailConfig(host="127.0.0.1", port=smtp.port, from_addr="radar@churn.example")
    stack = Stack(seeded, make_config(mail=mail))
    await stack.start()
    try:
        # Анна хочет знать о росте потери больше 5 % — в панели и на почту; Борису хватит 50 %
        await _subscribe(
            seeded,
            "anna",
            {
                "email": "anna@example.com",
                "events": {"loss_growth": {"threshold": 5, "period_h": 24, "channels": ["panel", "email"]}},
            },
        )
        await _subscribe(seeded, "boris", {"events": {"loss_growth": {"threshold": 50, "channels": ["panel"]}}})
        await _baseline(stack)

        # пересчёт: потеря выросла на 20 %, клиент 2 ушёл в высокий риск с маленькой потерей
        async with seeded.acquire() as conn:
            await put_summary(conn, loss=120_000, high=2, share=0.25)
            await put_score(conn, 2, "high", 600)
        event = scores_updated([2], 120_000)
        await stack.publish(event)
        await wait_for(lambda: stack.processed(event["event_id"]), what="пересчёт")

        anna = await _notifications(seeded, "anna")
        assert [n["event_type"] for n in anna] == ["loss_growth"]
        assert anna[0]["title"] == "Ожидаемая потеря выросла на 20\u00a0% за сутки"
        assert await _notifications(seeded, "boris") == []

        await wait_for(lambda: _true(len(smtp.messages) == 1), what="письмо")
        letter = smtp.messages[0]
        assert letter["to"] == ["anna@example.com"]
        assert letter["subject"] == "ChurnRadar: Ожидаемая потеря выросла на 20\u00a0% за сутки"
        html = letter["message"].get_body(preferencelist=("html",)).get_content()
        assert "https://churn.example/" in html and "Настроить уведомления" in html

        # журнал — по порогам по умолчанию (10 %): рост в нём есть, поклиентских записей нет
        assert [r["kind"] for r in await stack.feed()] == ["loss_growth"]

        # повтор того же события ничего не добавляет
        duplicates = EVENTS.labels(result="duplicate")._value.get()
        await stack.publish(event)
        await wait_for(lambda: _true(EVENTS.labels(result="duplicate")._value.get() > duplicates), what="повтор")
        await asyncio.sleep(0.3)
        assert len(await _notifications(seeded, "anna")) == 1 and len(smtp.messages) == 1

        # дальнейший рост в пределах анти-спама не шлём, хотя порог превышен
        async with seeded.acquire() as conn:
            await put_summary(conn, loss=140_000, high=2, share=0.25)
        again = scores_updated([], 140_000, full=True)
        await stack.publish(again)
        await wait_for(lambda: stack.processed(again["event_id"]), what="второй пересчёт")
        assert len(await _notifications(seeded, "anna")) == 1
    finally:
        await stack.stop()


async def test_big_customer_to_telegram_with_quiet_hours(seeded: Any, telegram: FakeTelegram) -> None:
    stack = Stack(seeded, make_config(telegram_bot_token="123:abc", telegram_api_url=telegram.url))
    await stack.start()
    try:
        big = {"big_customer_high": {"threshold": 100, "channels": ["telegram", "panel"]}}
        await _subscribe(seeded, "anna", {"events": big})
        # у Бориса тихие часы на весь день: сообщение ляжет в очередь до их конца
        await _subscribe(seeded, "boris", {"events": big, "quiet_from": "00:00", "quiet_to": "23:59"})
        async with seeded.acquire() as conn:
            await repo.link_chat(conn, "anna", 1001, "anna_tg")
            await repo.link_chat(conn, "boris", 1002, "boris_tg")
        await _baseline(stack)

        async with seeded.acquire() as conn:
            await put_score(conn, 1, "high", 50_000)  # €500 — выше порога €100
        event = scores_updated([1], 100_000)
        await stack.publish(event)
        await wait_for(lambda: stack.processed(event["event_id"]), what="пересчёт")

        await wait_for(lambda: _true(telegram.replies(1001)), what="сообщение Анне")
        sent = next(m for m in telegram.sent if m["chat_id"] == "1001")
        assert "Крупный клиент #1 (user1@example.com) перешёл в высокий риск" in sent["text"]
        assert sent["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://churn.example/customers/1"
        assert [n["link"] for n in await _notifications(seeded, "anna")] == ["/customers/1"]

        await asyncio.sleep(0.5)
        assert telegram.replies(1002) == []  # тихие часы
        async with seeded.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, next_attempt_at > now() AS later FROM alerts.deliveries WHERE username = 'boris'"
            )
        assert row["status"] == "pending" and row["later"]
        # в панели у Бориса запись есть сразу
        assert len(await _notifications(seeded, "boris")) == 1
    finally:
        await stack.stop()


async def test_journal_to_webhook_without_duplicates(seeded: Any, webhook: WebhookServer) -> None:
    stack = Stack(seeded, make_config(webhook_url=webhook.url + "/hook", webhook_secret="s"))
    await stack.start()
    try:
        await _baseline(stack)
        async with seeded.acquire() as conn:
            await put_summary(conn, loss=130_000, high=1, share=0.2)
        event = scores_updated([], 130_000, full=True)
        await stack.publish(event, msg_id=event["event_id"])
        await wait_for(lambda: _true(len(webhook.requests) == 1), what="webhook")
        hook = webhook.requests[0]
        assert hook["headers"]["X-ChurnRadar-Signature"].startswith("sha256=")
        assert [a["kind"] for a in hook["body"]["alerts"]] == ["loss_growth"]
        assert hook["body"]["alerts"][0]["url"] == "https://churn.example/"

        duplicates = EVENTS.labels(result="duplicate")._value.get()
        await stack.publish(event)
        await wait_for(lambda: _true(EVENTS.labels(result="duplicate")._value.get() > duplicates), what="повтор")
        await asyncio.sleep(0.5)
        assert len(webhook.requests) == 1 and len(await stack.feed()) == 1
    finally:
        await stack.stop()


async def test_webhook_down_retries_then_recovers(seeded: Any, webhook: WebhookServer) -> None:
    webhook.status = 503
    stack = Stack(seeded, make_config(webhook_url=webhook.url))
    await stack.start()
    failed_before = FAILED.labels(channel="webhook")._value.get()
    sent_before = SENT.labels(channel="webhook")._value.get()
    try:
        await _baseline(stack)
        async with seeded.acquire() as conn:
            await put_summary(conn, loss=150_000)
        await stack.publish(scores_updated([], 150_000, full=True))

        await wait_for(lambda: _true(len(webhook.requests) >= 3), what="повторные попытки")
        assert FAILED.labels(channel="webhook")._value.get() - failed_before >= 3
        async with seeded.acquire() as conn:
            row = await conn.fetchrow("SELECT status, attempts, last_error FROM alerts.deliveries")
        assert row["status"] == "pending" and row["attempts"] >= 2 and "503" in row["last_error"]
        assert len(await stack.feed()) == 1  # журнал не зависит от внешнего канала

        webhook.status = 200
        await wait_for(lambda: _true(SENT.labels(channel="webhook")._value.get() > sent_before), what="доставка")
    finally:
        await stack.stop()


async def test_mass_change_is_one_message_per_user(seeded: Any, telegram: FakeTelegram) -> None:
    """Сто клиентов разом ушли в высокий риск (новый снимок): одно сообщение, а не сто."""
    async with seeded.acquire() as conn:
        for cid in range(100, 200):
            await put_score(conn, cid, "medium", 1_000)
    stack = Stack(seeded, make_config(telegram_bot_token="123:abc", telegram_api_url=telegram.url))
    await stack.start()
    try:
        await _subscribe(seeded, "anna", {"events": {"big_customer_high": {"threshold": 50, "channels": ["telegram"]}}})
        async with seeded.acquire() as conn:
            await repo.link_chat(conn, "anna", 1001, None)
        await _baseline(stack)
        async with seeded.acquire() as conn:
            for cid in range(100, 200):
                await put_score(conn, cid, "high", 60_000 + cid)
        event = scores_updated(list(range(100, 200)), 100_000)
        await stack.publish(event)
        await wait_for(lambda: _true(telegram.replies(1001)), what="сообщение")
        await asyncio.sleep(0.5)
        replies = telegram.replies(1001)
        assert len(replies) == 1
        assert "100 крупных клиентов перешли в высокий риск" in replies[0]
        assert "#199" in replies[0] and "…и ещё 95" in replies[0]
        # журнал тоже одной записью
        assert [r["kind"] for r in await stack.feed()] == ["big_customer_high"]
    finally:
        await stack.stop()
