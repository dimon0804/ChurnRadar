"""Кому и куда: событие → подписчики по их порогам → лента в панели, почта, Telegram.

Здесь же журнал (общая лента alerts.feed и общий канал команды) — он считается по порогам
по умолчанию, как ещё один «подписчик».
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

import asyncpg
from svckit.logs import get_logger

from . import repository as repo
from .catalog import Prefs, default_prefs, in_quiet_hours
from .config import Config
from .messages import email_payload, telegram_payload
from .metrics import NOTIFICATIONS, RAISED, SUPPRESSED
from .notify import (
    Notice,
    Snapshot,
    Transition,
    baseline_time,
    check_big_customers,
    check_high_count,
    check_loss_growth,
    check_risk_share,
    check_stale,
    digest_notice,
)
from .rules import JournalEntry, team_text, webhook_payload

log = get_logger("alerts.fanout")

GROWTH_EVENTS = ("loss_growth", "high_count_growth")


@dataclass(slots=True)
class FanoutResult:
    """Сколько уведомлений создано за проход: по пользователям и в журнал."""

    personal: int = 0
    journal: list[JournalEntry] = field(default_factory=list)
    queued_external: bool = False


def _journal_prefs() -> Prefs:
    """Журнал ведётся по порогам по умолчанию, но по всем типам событий."""
    base = default_prefs(repo.JOURNAL_USER)
    return replace(base, events={code: replace(p, enabled=True) for code, p in base.events.items()})


def _entry(notice: Notice) -> JournalEntry:
    customer_id = None
    if notice.link.startswith("/customers/") and notice.link.rsplit("/", 1)[1].isdigit():
        customer_id = int(notice.link.rsplit("/", 1)[1])
    return JournalEntry(kind=notice.event_type, message=f"{notice.title}. {notice.text}", customer_id=customer_id)


class Fanout:
    def __init__(self, config: Config) -> None:
        self._config = config

    @property
    def cooldown(self) -> timedelta:
        return timedelta(seconds=self._config.cooldown_s)

    # --- доставка одному пользователю ---------------------------------------------------------

    async def deliver(
        self,
        conn: asyncpg.Connection,
        prefs: Prefs,
        notice: Notice,
        channels: Sequence[str],
        *,
        now: datetime,
        chats: dict[str, int],
        respect_quiet: bool = True,
    ) -> dict[str, str]:
        """Раскладывает уведомление по каналам. Возвращает {канал: "ok" или почему пропущен}."""
        cfg = self._config
        result: dict[str, str] = {}
        batch_id = str(uuid.uuid4())
        not_before = in_quiet_hours(prefs, now) if respect_quiet else None
        muted = respect_quiet and prefs.mute_until is not None and prefs.mute_until > now
        for channel in channels:
            if channel == "panel":
                await repo.add_notification(
                    conn,
                    username=prefs.username,
                    event_type=notice.event_type,
                    title=notice.title,
                    message=notice.text,
                    link=notice.link,
                    now=now,
                )
                result[channel] = "ok"
            elif muted:
                result[channel] = "пауза до " + prefs.mute_until.isoformat(timespec="minutes")  # type: ignore[union-attr]
            elif channel == "email":
                if not cfg.mail.enabled:
                    result[channel] = "почта не настроена на сервере"
                elif not prefs.email:
                    result[channel] = "не указан адрес почты"
                else:
                    await repo.enqueue_delivery(
                        conn,
                        batch_id=batch_id,
                        channel="email",
                        payload=email_payload(notice, cfg.public_url),
                        recipient=prefs.email,
                        username=prefs.username,
                        not_before=not_before,
                    )
                    result[channel] = "ok"
            elif channel == "telegram":
                chat = chats.get(prefs.username)
                if not cfg.bot_enabled:
                    result[channel] = "бот не настроен"
                elif chat is None:
                    result[channel] = "Telegram не привязан"
                else:
                    await repo.enqueue_delivery(
                        conn,
                        batch_id=batch_id,
                        channel="telegram",
                        payload=telegram_payload(notice, cfg.public_url),
                        recipient=str(chat),
                        username=prefs.username,
                        not_before=not_before,
                    )
                    result[channel] = "ok"
            if result.get(channel) == "ok":
                NOTIFICATIONS.labels(event=notice.event_type, channel=channel).inc()
        return result

    # --- проход по событию scores.updated ----------------------------------------------------

    async def on_scores(
        self,
        conn: asyncpg.Connection,
        *,
        now: datetime,
        cur: Snapshot,
        prev: Snapshot | None,
        transitions: Sequence[Transition],
        event_ids: Sequence[str],
    ) -> FanoutResult:
        result = FanoutResult()
        states = await repo.event_states(conn)
        chats = await repo.chats_by_user(conn)
        cache: dict[datetime, Snapshot | None] = {}

        async def base_at(moment: datetime) -> Snapshot | None:
            if moment not in cache:
                cache[moment] = await repo.snapshot_at(conn, moment)
            return cache[moment]

        for prefs in [_journal_prefs(), *await repo.all_prefs(conn)]:
            journal = prefs.username == repo.JOURNAL_USER
            for code in ("big_customer_high", "loss_growth", "high_count_growth", "risk_share"):
                pref = prefs.event(code)
                if not pref.enabled or (not journal and not pref.channels):
                    continue
                last_sent, _ = states.get((prefs.username, code), (None, False))
                cooling = code != "big_customer_high" and last_sent is not None and now - last_sent < self.cooldown
                notice: Notice | None
                if code == "big_customer_high":
                    notice = check_big_customers(transitions, pref)
                elif code == "risk_share":
                    notice = check_risk_share(cur, prev, pref)
                else:
                    base = await base_at(baseline_time(now, pref, last_sent))
                    notice = (check_loss_growth if code == "loss_growth" else check_high_count)(cur, base, pref)
                if notice is None:
                    continue
                if cooling:
                    SUPPRESSED.labels(kind=code).inc()
                    continue
                RAISED.labels(kind=code).inc()
                await repo.set_event_state(conn, prefs.username, code, last_sent_at=now, active=False)
                if journal:
                    result.journal.append(_entry(notice))
                else:
                    delivered = await self.deliver(conn, prefs, notice, pref.channels, now=now, chats=chats)
                    result.personal += 1
                    result.queued_external |= any(v == "ok" for k, v in delivered.items() if k != "panel")
        if result.journal:
            await self.journal(conn, result.journal, event_ids=event_ids, now=now)
            result.queued_external |= bool(self._config.enabled_channels())
        return result

    async def journal(
        self, conn: asyncpg.Connection, entries: Sequence[JournalEntry], *, event_ids: Sequence[str], now: datetime
    ) -> None:
        """Общий журнал и общий канал команды (webhook, TELEGRAM_CHAT_ID), как было до персональных настроек."""
        cfg = self._config
        batch_id = str(uuid.uuid4())
        event_id = event_ids[-1] if event_ids else str(uuid.uuid4())
        await repo.insert_journal(conn, entries, batch_id=batch_id, event_id=event_id, now=now)
        if cfg.webhook_enabled:
            payload = webhook_payload(
                entries, batch_id=batch_id, event_ids=event_ids, created_at=now, public_url=cfg.public_url
            )
            await repo.enqueue_delivery(conn, batch_id=batch_id, channel="webhook", payload=payload)
        if cfg.telegram_enabled:
            await repo.enqueue_delivery(
                conn,
                batch_id=batch_id,
                channel="telegram",
                payload={"text": team_text(entries, cfg.public_url)},
                recipient=cfg.telegram_chat_id,
            )

    # --- по расписанию: данные без пересчёта и дайджест ------------------------------------

    async def on_tick(self, conn: asyncpg.Connection, *, now: datetime) -> bool:
        """True — что-то поставлено во внешние каналы (будим отправку)."""
        queued = False
        last_change, scored_at = await repo.freshness(conn)
        states = await repo.event_states(conn)
        chats = await repo.chats_by_user(conn)
        subscribers = await repo.all_prefs(conn)
        journal = _journal_prefs()
        stale_entries: list[JournalEntry] = []

        for prefs in [journal, *subscribers]:
            pref = prefs.event("stale_data")
            if not pref.enabled:
                continue
            _, active = states.get((prefs.username, "stale_data"), (None, False))
            notice = check_stale(last_change, scored_at, now, pref)
            if notice is None:
                if active:
                    await repo.set_event_state(conn, prefs.username, "stale_data", last_sent_at=None, active=False)
                continue
            if active:
                continue  # уже сообщили, ждём, пока пересчёт догонит данные
            RAISED.labels(kind="stale_data").inc()
            await repo.set_event_state(conn, prefs.username, "stale_data", last_sent_at=now, active=True)
            if prefs.username == repo.JOURNAL_USER:
                stale_entries.append(_entry(notice))
            else:
                delivered = await self.deliver(conn, prefs, notice, pref.channels, now=now, chats=chats)
                queued |= any(v == "ok" for k, v in delivered.items() if k != "panel")
        if stale_entries:
            await self.journal(conn, stale_entries, event_ids=[], now=now)
            queued |= bool(self._config.enabled_channels())

        cur: Snapshot | None = None
        for prefs in subscribers:
            if not prefs.digest_enabled or not prefs.digest_channels:
                continue
            local = now.astimezone(prefs.tz())
            if local.time() < prefs.digest_time:
                continue
            if not await repo.claim_digest(conn, prefs.username, local.date()):
                continue
            if cur is None:
                cur = await repo.current_metrics(conn, now)
                if cur is None:
                    break
            day_ago = await repo.snapshot_at(conn, now - timedelta(days=1))
            top = await repo.top_high(conn, 5)
            events = await repo.notifications_since(conn, now - timedelta(days=1))
            notice = digest_notice(cur, day_ago, top, events)
            delivered = await self.deliver(conn, prefs, notice, prefs.digest_channels, now=now, chats=chats)
            queued |= any(v == "ok" for v in delivered.values())
            log.info("digest_queued", username=prefs.username, channels=delivered)
        return queued


def describe(result: dict[str, str]) -> list[dict[str, Any]]:
    return [{"channel": k, "ok": v == "ok", "reason": None if v == "ok" else v} for k, v in result.items()]
