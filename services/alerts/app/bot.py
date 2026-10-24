"""Telegram-бот: привязка аккаунта панели по deep link и команды для быстрых вопросов.

Long polling (getUpdates), публичный URL не нужен. Опрос ведёт ровно одна реплика: она держит
сессионный pg_try_advisory_lock на отдельном соединении. Упала реплика — соединение закрылось,
lock освободился, через несколько секунд опрос подхватывает соседняя. Смещение getUpdates лежит
в alerts.telegram_state, поэтому новый лидер продолжает с того же места.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

import asyncpg
from svckit.logs import get_logger

from . import repository as repo
from .channels import DeliveryError, TelegramApi
from .config import Config
from .messages import public_link_ok
from .metrics import BOT_LEADER, BOT_POLL_ERRORS, BOT_UPDATES
from .notify import pct
from .rules import BULK_PATH, money

log = get_logger("alerts.bot")

LEVEL = {"high": "высокий", "medium": "средний", "low": "низкий", "unknown": "не определён"}

HELP = (
    "Я бот ChurnRadar: присылаю уведомления о критических событиях и отвечаю на короткие вопросы.\n\n"
    "/top — пять клиентов с самой большой ожидаемой потерей\n"
    "/customer &lt;id&gt; — карточка клиента\n"
    "/summary — состояние базы\n"
    "/mute &lt;часы&gt; — помолчать (0 — снова присылать)\n"
    "/stop — отвязать этот чат\n\n"
    "Какие события присылать, настраивается в панели: раздел «Уведомления»."
)
NOT_LINKED = (
    "Этот чат не привязан к аккаунту ChurnRadar. Откройте в панели «Уведомления» → Telegram → "
    "«Привязать» и нажмите Start в открывшемся чате."
)


class Bot:
    def __init__(self, config: Config, pool: asyncpg.Pool, api: TelegramApi) -> None:
        self._config = config
        self._pool = pool
        self._api = api
        self._stop = asyncio.Event()
        self.username: str | None = None
        self.leader = False

    def stop(self) -> None:
        self._stop.set()

    async def _sleep(self, seconds: float) -> bool:
        """True — пора выходить."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        return self._stop.is_set()

    async def resolve_username(self) -> str | None:
        """Имя бота для deep link; getMe может спрашивать любая реплика."""
        if self.username is None:
            with contextlib.suppress(DeliveryError):
                me = await self._api.call("getMe")
                self.username = (me or {}).get("username")
        return self.username

    async def run(self, *, retry_s: float = 5.0) -> None:
        """Пытается стать лидером; лидер опрашивает Telegram, остальные ждут своей очереди."""
        while not self._stop.is_set():
            try:
                async with self._pool.acquire() as conn:
                    if await conn.fetchval("SELECT pg_try_advisory_lock($1)", repo.BOT_LOCK_KEY):
                        self.leader = True
                        BOT_LEADER.set(1)
                        log.info("bot_leader")
                        try:
                            await self._poll()
                        finally:
                            self.leader = False
                            BOT_LEADER.set(0)
                            with contextlib.suppress(Exception):
                                await conn.execute("SELECT pg_advisory_unlock($1)", repo.BOT_LOCK_KEY)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("bot_loop_failed")
            if await self._sleep(retry_s):
                return

    async def _poll(self) -> None:
        await self.resolve_username()
        async with self._pool.acquire() as conn:
            offset = await repo.get_offset(conn)
        timeout = self._config.telegram_poll_timeout_s
        while not self._stop.is_set():
            try:
                updates = await self._api.call(
                    "getUpdates",
                    {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
                    timeout=timeout + 10,
                )
            except DeliveryError as exc:
                BOT_POLL_ERRORS.inc()
                log.warning("bot_poll_failed", error=str(exc))
                if await self._sleep(5):
                    return
                continue
            for update in updates or []:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                try:
                    await self.handle(update)
                except Exception:
                    log.exception("bot_update_failed")
            if updates:
                async with self._pool.acquire() as conn:
                    await repo.set_offset(conn, offset)

    # --- команды ------------------------------------------------------------------------------

    async def reply(self, chat_id: int, text: str, *, path: str | None = None) -> None:
        button = None
        if path is not None:
            link = self._config.public_url + path
            if public_link_ok(link):
                button = {"text": "Открыть в панели", "url": link}
            else:
                text += f"\n\n{escape(link)}"
        await self._api.send_message(chat_id, text, parse_mode="HTML", button=button)

    async def handle(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip()
        if chat_id is None or not text.startswith("/"):
            return
        command, _, arg = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        arg = arg.strip()
        BOT_UPDATES.labels(command=command if command in COMMANDS else "other").inc()
        tg_username = (message.get("from") or {}).get("username")

        async with self._pool.acquire() as conn:
            if command == "/start" and arg:
                username = await repo.consume_link_code(conn, arg)
                if username is None:
                    await self.reply(
                        chat_id,
                        "Ссылка устарела или уже использована. Получите новую в настройках уведомлений в панели.",
                    )
                    return
                await repo.link_chat(conn, username, int(chat_id), tg_username)
                log.info("telegram_linked", username=username)
                await self.reply(
                    chat_id,
                    f"Готово: чат привязан к аккаунту <b>{escape(username)}</b>.\n"
                    "Теперь в настройках уведомлений можно включить канал Telegram.\n\n" + HELP,
                    path="/notifications",
                )
                return
            if command in ("/start", "/help"):
                await self.reply(chat_id, HELP)
                return
            username = await repo.user_of_chat(conn, int(chat_id))
            if username is None:
                await self.reply(chat_id, NOT_LINKED)
                return
            if command == "/stop":
                await repo.unlink(conn, chat_id=int(chat_id))
                await self.reply(chat_id, "Чат отвязан, уведомлений сюда больше не будет.")
            elif command == "/top":
                await self._top(conn, chat_id)
            elif command == "/customer":
                await self._customer(conn, chat_id, arg)
            elif command == "/summary":
                await self._summary(conn, chat_id)
            elif command == "/mute":
                await self._mute(conn, chat_id, username, arg)
            else:
                await self.reply(chat_id, "Не знаю такой команды.\n\n" + HELP)

    async def _top(self, conn: asyncpg.Connection, chat_id: int) -> None:
        top = await repo.top_high(conn, 5)
        if not top:
            await self.reply(chat_id, "Сейчас в высоком риске никого нет.")
            return
        lines = [
            f"{i}. #{t.customer_id} {escape(t.email or '')} — {money(t.expected_loss)}" for i, t in enumerate(top, 1)
        ]
        await self.reply(chat_id, "<b>Топ-5 по ожидаемой потере</b>\n" + "\n".join(lines), path=BULK_PATH)

    async def _customer(self, conn: asyncpg.Connection, chat_id: int, arg: str) -> None:
        if not arg.lstrip("#").isdigit():
            await self.reply(chat_id, "Напишите номер клиента: /customer 42")
            return
        card = await repo.customer_card(conn, int(arg.lstrip("#")))
        if card is None:
            await self.reply(chat_id, f"Клиента #{escape(arg)} нет в базе.")
            return
        reasons = [r.get("label") or r.get("title") for r in card["reasons"][:3] if isinstance(r, dict)]
        score = card.get("risk_score")
        lines = [
            f"<b>Клиент #{card['customer_id']}</b> {escape(card.get('email') or '')}",
            f"Риск: {LEVEL.get(card['risk_level'], card['risk_level'])}"
            + (f" ({score:.0f} из 100)" if isinstance(score, int | float) else ""),
            f"Ожидаемая потеря: {money(card['expected_loss'])}",
            f"Выручка в месяц: {money(card['monthly_revenue'])}",
        ]
        if reasons:
            lines.append("Почему: " + "; ".join(escape(str(r)) for r in reasons if r))
        await self.reply(chat_id, "\n".join(lines), path=f"/customers/{card['customer_id']}")

    async def _summary(self, conn: asyncpg.Connection, chat_id: int) -> None:
        cur = await repo.current_metrics(conn, datetime.now(UTC))
        if cur is None:
            await self.reply(chat_id, "Оценок пока нет: пересчёт ещё не прошёл.")
            return
        await self.reply(
            chat_id,
            "<b>Состояние базы</b>\n"
            f"Клиентов: {cur.customers}, в высоком риске: {cur.high_count}\n"
            f"Ожидаемая потеря: {money(cur.expected_loss)}\n"
            f"Под риском {pct(cur.risk_share * 100)} выручки",
            path="/",
        )

    async def _mute(self, conn: asyncpg.Connection, chat_id: int, username: str, arg: str) -> None:
        try:
            hours = float(arg.replace(",", ".")) if arg else 1.0
        except ValueError:
            hours = -1
        if not 0 <= hours <= 24 * 7:
            await self.reply(chat_id, "Сколько часов помолчать? Например: /mute 3 (до 168, 0 — снять паузу).")
            return
        until = None if hours == 0 else datetime.now(UTC) + timedelta(hours=hours)
        await repo.set_mute(conn, username, until)
        if until is None:
            await self.reply(chat_id, "Пауза снята, уведомления снова приходят.")
        else:
            await self.reply(
                chat_id, f"Помолчу {hours:g} ч. Письма и сообщения сюда не придут, в панели всё останется."
            )


COMMANDS = ("/start", "/help", "/top", "/customer", "/summary", "/mute", "/stop")


def deep_link(bot_username: str, code: str) -> str:
    return f"https://t.me/{bot_username}?start={code}"
