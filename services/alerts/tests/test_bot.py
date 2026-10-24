"""Telegram-бот против подставного Bot API и настоящего Postgres: привязка, команды, лидерство."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app import repository as repo
from app.bot import Bot, deep_link
from app.channels import TelegramApi
from app.config import Config
from conftest import TEST_DATABASE_URL, put_score
from fakes import FakeTelegram, put_summary

pytestmark = pytest.mark.integration


async def wait_until(check: Any, timeout: float = 10.0, what: str = "условие") -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"не дождались: {what}")
        await asyncio.sleep(0.05)


def make_bot(pool: Any, client: httpx.AsyncClient, telegram: FakeTelegram) -> Bot:
    config = Config(
        database_url=TEST_DATABASE_URL,
        telegram_bot_token="123:abc",
        telegram_api_url=telegram.url,
        telegram_poll_timeout_s=1,
        public_url="https://churn.example",
    )
    return Bot(config, pool, TelegramApi(client, "123:abc", api_url=telegram.url))


async def test_link_commands_and_single_leader(db_pool: Any, telegram: FakeTelegram) -> None:
    async with db_pool.acquire() as conn:
        await put_score(conn, 3, "high", 20_000)
        await put_score(conn, 4, "high", 90_000)
        await put_summary(conn, loss=110_000, high=2, share=0.31, customers=4)
        code = await repo.create_link_code(conn, "anna", timedelta(minutes=10))
        expired = await repo.create_link_code(conn, "boris", timedelta(seconds=-1))

    async with httpx.AsyncClient() as client:
        bots = [make_bot(db_pool, client, telegram), make_bot(db_pool, client, telegram)]
        assert await bots[0].resolve_username() == "churn_test_bot"
        assert deep_link("churn_test_bot", code) == f"https://t.me/churn_test_bot?start={code}"
        tasks = [asyncio.create_task(b.run(retry_s=0.2)) for b in bots]
        try:
            # getUpdates ведёт ровно одна реплика
            async def one_leader() -> bool:
                return sum(b.leader for b in bots) == 1

            await wait_until(one_leader, what="лидер")
            await asyncio.sleep(0.5)
            assert sum(b.leader for b in bots) == 1

            telegram.say(2001, "/top")  # чат ещё не привязан — данных не даём
            telegram.say(2001, f"/start {code}")
            telegram.say(2002, f"/start {expired}")
            telegram.say(2001, "/top")
            telegram.say(2001, "/customer 4")
            telegram.say(2001, "/summary")
            telegram.say(2001, "/mute 2")

            async def answered() -> bool:
                return len(telegram.replies(2001)) >= 6 and len(telegram.replies(2002)) >= 1

            await wait_until(answered, what="ответы бота")
            replies = telegram.replies(2001)
            assert "не привязан" in replies[0]
            assert "привязан к аккаунту <b>anna</b>" in replies[1]
            assert "Топ-5" in replies[2] and replies[2].index("#4") < replies[2].index("#3")
            assert "Клиент #4" in replies[3] and "€900" in replies[3]
            assert "в высоком риске: 2" in replies[4] and "31" in replies[4]
            assert "Помолчу 2 ч" in replies[5]
            assert "устарела" in telegram.replies(2002)[0]

            card = next(m for m in telegram.sent if "Клиент #4" in m["text"])
            assert card["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://churn.example/customers/4"
            async with db_pool.acquire() as conn:
                assert await repo.user_of_chat(conn, 2001) == "anna"
                prefs = await repo.get_prefs(conn, "anna")
                assert prefs.mute_until is not None and prefs.mute_until > datetime.now(UTC) + timedelta(hours=1)
                # код одноразовый
                assert await repo.consume_link_code(conn, code) is None
                assert await repo.get_offset(conn) == 8

            # лидер остановился — опрос подхватывает другая реплика
            leader = next(b for b in bots if b.leader)
            leader.stop()

            async def other_leads() -> bool:
                return any(b.leader for b in bots if b is not leader)

            await wait_until(other_leads, what="смена лидера")
            telegram.say(2001, "/stop")

            async def unlinked() -> bool:
                async with db_pool.acquire() as conn:
                    return await repo.user_of_chat(conn, 2001) is None

            await wait_until(unlinked, what="отвязка")
        finally:
            for b in bots:
                b.stop()
            await asyncio.gather(*tasks, return_exceptions=True)
