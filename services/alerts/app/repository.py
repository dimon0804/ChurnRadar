"""Postgres: scoring.* и source.* только читаем, пишем только в схему alerts."""

from __future__ import annotations

import json
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import asyncpg
from svckit import db

from .catalog import Prefs, default_prefs, prefs_from_row, prefs_to_storage
from .notify import Snapshot, Transition
from .rules import JournalEntry

# Один обработчик пачки за раз на весь кластер: реплики не спорят о состоянии клиентов.
LOCK_KEY = 7_340_004
# Лидер long polling Telegram: getUpdates ведёт ровно одна реплика, иначе Telegram отвечает 409.
BOT_LOCK_KEY = 7_340_005
JOURNAL_USER = "__journal__"


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 6) -> asyncpg.Pool:
    return await db.create_pool(dsn, min_size, max_size, timeout_s=0, json_codec=True)


async def lock(conn: asyncpg.Connection, timeout_s: float = 30.0) -> None:
    """Транзакционный advisory lock: снимается коммитом, откатом или обрывом соединения."""
    await conn.execute("SELECT set_config('lock_timeout', $1, true)", f"{int(timeout_s * 1000)}ms")
    await conn.execute("SELECT pg_advisory_xact_lock($1)", LOCK_KEY)


# --- идемпотентность ----------------------------------------------------------------------------


async def processed_event_ids(conn: asyncpg.Connection, event_ids: Sequence[str]) -> set[str]:
    if not event_ids:
        return set()
    rows = await conn.fetch(
        "SELECT event_id::text FROM alerts.processed_events WHERE event_id = ANY($1::uuid[])", list(event_ids)
    )
    return {r[0] for r in rows}


async def mark_processed(conn: asyncpg.Connection, event_ids: Sequence[str]) -> None:
    if event_ids:
        await conn.execute(
            "INSERT INTO alerts.processed_events (event_id) SELECT unnest($1::uuid[]) ON CONFLICT DO NOTHING",
            list(event_ids),
        )


# --- клиенты: кто перешёл в высокий риск ---------------------------------------------------------


async def baseline_taken(conn: asyncpg.Connection) -> bool:
    await conn.execute("INSERT INTO alerts.meta (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
    return await conn.fetchval("SELECT baseline_at FROM alerts.meta WHERE id = 1") is not None


async def take_baseline(conn: asyncpg.Connection, now: datetime) -> int:
    """Первый запуск: запоминаем всех как есть и ничего не шлём, иначе первым же событием
    пришли бы уведомления обо всех, кто уже в высоком риске."""
    await conn.execute("TRUNCATE alerts.customer_state")
    status = await conn.execute(
        "INSERT INTO alerts.customer_state (customer_id, risk_level, expected_loss, loss_ref) "
        "SELECT customer_id, risk_level, expected_loss, expected_loss FROM scoring.customer_scores"
    )
    await conn.execute("UPDATE alerts.meta SET baseline_at = $1, updated_at = now() WHERE id = 1", now)
    return int(status.split()[-1])


async def high_transitions(
    conn: asyncpg.Connection, customer_ids: Sequence[int] | None, now: datetime, cooldown: timedelta
) -> list[Transition]:
    """Обновляет запомненное состояние клиентов и возвращает тех, кто перешёл в высокий риск.

    Сравнение одним запросом в базе: в Python попадают только клиенты, у которых что-то поменялось.
    Клиент, который уже переходил в высокий риск меньше cooldown назад (качели туда-обратно),
    повторно не возвращается.
    """
    ids = None if customer_ids is None else list(customer_ids)
    await conn.execute(
        """
        DELETE FROM alerts.customer_state st
        WHERE ($1::bigint[] IS NULL OR st.customer_id = ANY($1::bigint[]))
          AND NOT EXISTS (SELECT 1 FROM scoring.customer_scores s WHERE s.customer_id = st.customer_id)
        """,
        ids,
    )
    rows = await conn.fetch(
        """
        WITH changed AS (
            SELECT s.customer_id, s.email, s.risk_level, s.expected_loss,
                   st.risk_level AS prev_level, st.last_alert_at
            FROM scoring.customer_scores s
            LEFT JOIN alerts.customer_state st ON st.customer_id = s.customer_id
            WHERE ($1::bigint[] IS NULL OR s.customer_id = ANY($1::bigint[]))
              AND (st.customer_id IS NULL
                   OR st.risk_level IS DISTINCT FROM s.risk_level
                   OR st.expected_loss IS DISTINCT FROM s.expected_loss)
        ), upserted AS (
            INSERT INTO alerts.customer_state AS cs (customer_id, risk_level, expected_loss, loss_ref, last_alert_at)
            SELECT c.customer_id, c.risk_level, c.expected_loss, c.expected_loss,
                   CASE WHEN c.risk_level = 'high' AND c.prev_level IS DISTINCT FROM 'high'
                             AND (c.last_alert_at IS NULL OR c.last_alert_at < $2::timestamptz - $3::interval)
                        THEN $2::timestamptz ELSE c.last_alert_at END
            FROM changed c
            ON CONFLICT (customer_id) DO UPDATE SET
                risk_level = EXCLUDED.risk_level, expected_loss = EXCLUDED.expected_loss,
                loss_ref = EXCLUDED.loss_ref, last_alert_at = EXCLUDED.last_alert_at, updated_at = now()
        )
        SELECT customer_id, email, expected_loss, prev_level FROM changed
        WHERE risk_level = 'high' AND prev_level IS DISTINCT FROM 'high'
          AND (last_alert_at IS NULL OR last_alert_at < $2::timestamptz - $3::interval)
        """,
        ids,
        now,
        cooldown,
    )
    return [Transition(r["customer_id"], r["email"], int(r["expected_loss"]), r["prev_level"]) for r in rows]


async def top_high(conn: asyncpg.Connection, limit: int = 5) -> list[Transition]:
    rows = await conn.fetch(
        "SELECT customer_id, email, expected_loss, risk_level FROM scoring.customer_scores "
        "WHERE risk_level = 'high' ORDER BY expected_loss DESC LIMIT $1",
        limit,
    )
    return [Transition(r["customer_id"], r["email"], int(r["expected_loss"]), r["risk_level"]) for r in rows]


async def customer_card(conn: asyncpg.Connection, customer_id: int) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "SELECT customer_id, email, risk_level, risk_score, expected_loss, monthly_revenue, reasons "
        "FROM scoring.customer_scores WHERE customer_id = $1",
        customer_id,
    )
    if row is None:
        return None
    card = dict(row)
    reasons = card.get("reasons")
    card["reasons"] = json.loads(reasons) if isinstance(reasons, str) else (reasons or [])
    return card


# --- показатели базы -----------------------------------------------------------------------------


async def current_metrics(conn: asyncpg.Connection, now: datetime) -> Snapshot | None:
    row = await conn.fetchrow("SELECT data FROM scoring.summary WHERE id = 1")
    if row is None:
        return None
    data = row["data"]
    if isinstance(data, str):
        data = json.loads(data)
    by_level = data.get("by_level") or {}
    return Snapshot(
        at=now,
        expected_loss=int(data.get("expected_loss") or 0),
        high_count=int(by_level.get("high") or 0),
        risk_share=float(data.get("revenue_at_risk_share") or 0.0),
        customers=int(data.get("customers_total") or 0),
    )


def _snapshot(row: Any) -> Snapshot:
    return Snapshot(
        row["at"], int(row["expected_loss"]), int(row["high_count"]), float(row["risk_share"]), int(row["customers"])
    )


async def latest_snapshot(conn: asyncpg.Connection) -> Snapshot | None:
    row = await conn.fetchrow("SELECT * FROM alerts.metric_snapshots ORDER BY at DESC, id DESC LIMIT 1")
    return None if row is None else _snapshot(row)


async def snapshot_at(conn: asyncpg.Connection, moment: datetime) -> Snapshot | None:
    """Показатели на момент: последние до него, а если истории столько нет — самые ранние."""
    row = await conn.fetchrow(
        "SELECT * FROM alerts.metric_snapshots WHERE at <= $1 ORDER BY at DESC, id DESC LIMIT 1", moment
    )
    if row is None:
        row = await conn.fetchrow("SELECT * FROM alerts.metric_snapshots ORDER BY at, id LIMIT 1")
    return None if row is None else _snapshot(row)


async def write_snapshot(conn: asyncpg.Connection, snap: Snapshot, every: timedelta) -> None:
    """Не чаще раза в every: свежая запись обновляется на месте, так история не растёт с каждым пересчётом."""
    updated = await conn.execute(
        """
        UPDATE alerts.metric_snapshots SET expected_loss = $1, high_count = $2, risk_share = $3, customers = $4
        WHERE id = (SELECT id FROM alerts.metric_snapshots ORDER BY at DESC, id DESC LIMIT 1) AND at > $5
        """,
        snap.expected_loss,
        snap.high_count,
        snap.risk_share,
        snap.customers,
        snap.at - every,
    )
    if updated.endswith(" 0"):
        await conn.execute(
            "INSERT INTO alerts.metric_snapshots (at, expected_loss, high_count, risk_share, customers) "
            "VALUES ($1, $2, $3, $4, $5)",
            snap.at,
            snap.expected_loss,
            snap.high_count,
            snap.risk_share,
            snap.customers,
        )


async def freshness(conn: asyncpg.Connection) -> tuple[datetime | None, datetime | None]:
    """(время последнего изменения источника, время последнего пересчёта)."""
    last_change = await conn.fetchval("SELECT max(created_at) FROM source.snapshots")
    scored_at = await conn.fetchval("SELECT scored_at FROM scoring.summary WHERE id = 1")
    return last_change, scored_at


# --- настройки пользователей ---------------------------------------------------------------------


_PREFS_COLUMNS = (
    "username, email, timezone, quiet_from, quiet_to, digest_enabled, digest_time, digest_channels, "
    "last_digest_on, mute_until, events, updated_at"
)


async def get_prefs(conn: asyncpg.Connection, username: str, *, create: bool = True) -> Prefs:
    """Настройки пользователя; при первом обращении заводим строку с настройками по умолчанию,
    чтобы колокольчик работал сразу, без похода в настройки."""
    row = await conn.fetchrow(f"SELECT {_PREFS_COLUMNS} FROM alerts.user_prefs WHERE username = $1", username)
    if row is not None:
        return prefs_from_row(dict(row))
    prefs = default_prefs(username)
    if create:
        await conn.execute(
            "INSERT INTO alerts.user_prefs (username, events) VALUES ($1, $2) ON CONFLICT (username) DO NOTHING",
            username,
            prefs_to_storage(prefs),
        )
    return prefs


async def save_prefs(conn: asyncpg.Connection, prefs: Prefs, updated_by: str) -> None:
    await conn.execute(
        """
        INSERT INTO alerts.user_prefs (username, email, timezone, quiet_from, quiet_to, digest_enabled,
                                       digest_time, digest_channels, events, updated_at, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now(), $10)
        ON CONFLICT (username) DO UPDATE SET
            email = EXCLUDED.email, timezone = EXCLUDED.timezone, quiet_from = EXCLUDED.quiet_from,
            quiet_to = EXCLUDED.quiet_to, digest_enabled = EXCLUDED.digest_enabled,
            digest_time = EXCLUDED.digest_time, digest_channels = EXCLUDED.digest_channels,
            events = EXCLUDED.events, updated_at = now(), updated_by = EXCLUDED.updated_by
        """,
        prefs.username,
        prefs.email,
        prefs.timezone,
        prefs.quiet_from,
        prefs.quiet_to,
        prefs.digest_enabled,
        prefs.digest_time,
        list(prefs.digest_channels),
        prefs_to_storage(prefs),
        updated_by,
    )


async def reset_prefs(conn: asyncpg.Connection, username: str) -> bool:
    status = await conn.execute("DELETE FROM alerts.user_prefs WHERE username = $1", username)
    await conn.execute("DELETE FROM alerts.user_event_state WHERE username = $1", username)
    return not status.endswith(" 0")


async def all_prefs(conn: asyncpg.Connection) -> list[Prefs]:
    rows = await conn.fetch(f"SELECT {_PREFS_COLUMNS} FROM alerts.user_prefs ORDER BY username")
    return [prefs_from_row(dict(r)) for r in rows]


async def prefs_overview(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    """Для администратора: кто что настроил."""
    rows = await conn.fetch(
        f"""
        SELECT {_PREFS_COLUMNS}, updated_by, tl.tg_username, tl.chat_id IS NOT NULL AS telegram_linked
        FROM alerts.user_prefs p LEFT JOIN alerts.telegram_links tl USING (username)
        ORDER BY username
        """
    )
    return [dict(r) for r in rows]


async def set_mute(conn: asyncpg.Connection, username: str, until: datetime | None) -> None:
    await get_prefs(conn, username)
    await conn.execute("UPDATE alerts.user_prefs SET mute_until = $2 WHERE username = $1", username, until)


async def claim_digest(conn: asyncpg.Connection, username: str, local_date: Any) -> bool:
    """Атомарно: сегодняшний дайджест отправляет ровно одна реплика."""
    status = await conn.execute(
        "UPDATE alerts.user_prefs SET last_digest_on = $2 "
        "WHERE username = $1 AND (last_digest_on IS NULL OR last_digest_on < $2)",
        username,
        local_date,
    )
    return not status.endswith(" 0")


async def event_states(conn: asyncpg.Connection) -> dict[tuple[str, str], tuple[datetime | None, bool]]:
    rows = await conn.fetch("SELECT username, event_type, last_sent_at, active FROM alerts.user_event_state")
    return {(r["username"], r["event_type"]): (r["last_sent_at"], r["active"]) for r in rows}


async def set_event_state(
    conn: asyncpg.Connection, username: str, event_type: str, *, last_sent_at: datetime | None, active: bool
) -> None:
    await conn.execute(
        """
        INSERT INTO alerts.user_event_state (username, event_type, last_sent_at, active) VALUES ($1, $2, $3, $4)
        ON CONFLICT (username, event_type) DO UPDATE SET
            last_sent_at = coalesce(EXCLUDED.last_sent_at, alerts.user_event_state.last_sent_at),
            active = EXCLUDED.active
        """,
        username,
        event_type,
        last_sent_at,
        active,
    )


# --- личная лента (канал «в панели») -------------------------------------------------------------


async def add_notification(
    conn: asyncpg.Connection, *, username: str, event_type: str, title: str, message: str, link: str, now: datetime
) -> None:
    await conn.execute(
        "INSERT INTO alerts.notifications (username, event_type, title, message, link, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        username,
        event_type,
        title,
        message,
        link,
        now,
    )


async def list_notifications(pool: asyncpg.Pool, username: str, limit: int) -> tuple[list[dict[str, Any]], int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, event_type, title, message, link, created_at, read_at FROM alerts.notifications "
            "WHERE username = $1 ORDER BY id DESC LIMIT $2",
            username,
            limit,
        )
        unread = await conn.fetchval(
            "SELECT count(*) FROM alerts.notifications WHERE username = $1 AND read_at IS NULL", username
        )
    return [dict(r) for r in rows], int(unread or 0)


async def mark_read(pool: asyncpg.Pool, username: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE alerts.notifications SET read_at = now() WHERE username = $1 AND read_at IS NULL", username
        )


async def notifications_since(conn: asyncpg.Connection, since: datetime) -> int:
    return int(await conn.fetchval("SELECT count(*) FROM alerts.feed WHERE created_at >= $1", since) or 0)


# --- журнал (alerts.feed) ------------------------------------------------------------------------


async def insert_journal(
    conn: asyncpg.Connection, entries: Sequence[JournalEntry], *, batch_id: str, event_id: str, now: datetime
) -> None:
    if entries:
        await conn.executemany(
            "INSERT INTO alerts.feed (batch_id, event_id, kind, customer_id, expected_loss, prev_loss, message, "
            "created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            [
                (batch_id, event_id, e.kind, e.customer_id, e.expected_loss, e.prev_loss, e.message, now)
                for e in entries
            ],
        )


async def list_feed(
    pool: asyncpg.Pool, *, page: int, page_size: int, customer_id: int | None, kind: str | None
) -> tuple[list[dict[str, Any]], int, int | None]:
    """Страница журнала, всего записей по фильтру и id самой свежей записи."""
    where = "WHERE ($1::bigint IS NULL OR customer_id = $1) AND ($2::text IS NULL OR kind = $2)"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, kind, customer_id, email, risk_level, prev_level, expected_loss, prev_loss, message, created_at
            FROM alerts.feed {where} ORDER BY id DESC LIMIT $3 OFFSET $4
            """,
            customer_id,
            kind,
            page_size,
            (page - 1) * page_size,
        )
        total = await conn.fetchval(f"SELECT count(*) FROM alerts.feed {where}", customer_id, kind)
        latest = await conn.fetchval("SELECT max(id) FROM alerts.feed")
    return [dict(r) for r in rows], int(total or 0), latest


# --- доставка ------------------------------------------------------------------------------------


async def enqueue_delivery(
    conn: asyncpg.Connection,
    *,
    batch_id: str,
    channel: str,
    payload: dict[str, Any],
    recipient: str | None = None,
    username: str | None = None,
    not_before: datetime | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO alerts.deliveries (batch_id, channel, payload, recipient, username, next_attempt_at) "
        "VALUES ($1, $2, $3, $4, $5, coalesce($6, now())) ON CONFLICT DO NOTHING",
        batch_id,
        channel,
        payload,
        recipient,
        username,
        not_before,
    )


@dataclass(frozen=True, slots=True)
class Delivery:
    id: int
    channel: str
    payload: dict[str, Any]
    attempts: int
    batch_id: str
    recipient: str | None = None


async def claim_deliveries(pool: asyncpg.Pool, limit: int, lease_s: float) -> list[Delivery]:
    """Берёт доставки, чей срок подошёл, и сдвигает им срок на lease: другая реплика их не возьмёт,
    а если эта упадёт посреди отправки, через lease доставку подхватят снова."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE alerts.deliveries d
            SET next_attempt_at = now() + make_interval(secs => $2)
            WHERE d.id IN (
                SELECT id FROM alerts.deliveries
                WHERE status = 'pending' AND next_attempt_at <= now()
                ORDER BY id
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING d.id, d.channel, d.payload, d.attempts, d.batch_id::text, d.recipient
            """,
            limit,
            float(lease_s),
        )
    out = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append(Delivery(r["id"], r["channel"], payload, r["attempts"], r["batch_id"], r["recipient"]))
    return sorted(out, key=lambda d: d.id)


async def mark_sent(pool: asyncpg.Pool, delivery_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE alerts.deliveries SET status = 'sent', attempts = attempts + 1, sent_at = now(), "
            "last_error = NULL WHERE id = $1",
            delivery_id,
        )


async def mark_failed(pool: asyncpg.Pool, delivery_id: int, error: str, *, retry_in_s: float | None) -> None:
    """retry_in_s=None — больше не пробуем (status failed)."""
    async with pool.acquire() as conn:
        if retry_in_s is None:
            await conn.execute(
                "UPDATE alerts.deliveries SET status = 'failed', attempts = attempts + 1, last_error = $2 "
                "WHERE id = $1",
                delivery_id,
                error[:1000],
            )
        else:
            await conn.execute(
                "UPDATE alerts.deliveries SET attempts = attempts + 1, last_error = $2, "
                "next_attempt_at = now() + make_interval(secs => $3) WHERE id = $1",
                delivery_id,
                error[:1000],
                float(retry_in_s),
            )


async def pending_count(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return int(await conn.fetchval("SELECT count(*) FROM alerts.deliveries WHERE status = 'pending'"))


async def prune(pool: asyncpg.Pool, retention: timedelta, snapshot_retention: timedelta) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM alerts.feed WHERE created_at < now() - $1::interval", retention)
        await conn.execute("DELETE FROM alerts.notifications WHERE created_at < now() - $1::interval", retention)
        await conn.execute(
            "DELETE FROM alerts.deliveries WHERE status <> 'pending' AND created_at < now() - $1::interval", retention
        )
        await conn.execute("DELETE FROM alerts.metric_snapshots WHERE at < now() - $1::interval", snapshot_retention)
        await conn.execute("DELETE FROM alerts.processed_events WHERE processed_at < now() - interval '2 days'")
        await conn.execute("DELETE FROM alerts.telegram_codes WHERE expires_at < now()")


# --- Telegram ------------------------------------------------------------------------------------


async def create_link_code(conn: asyncpg.Connection, username: str, ttl: timedelta) -> str:
    code = secrets.token_urlsafe(18)  # для /start допустимы A-Z a-z 0-9 _ -, до 64 символов
    await conn.execute("DELETE FROM alerts.telegram_codes WHERE username = $1", username)
    await conn.execute(
        "INSERT INTO alerts.telegram_codes (code, username, expires_at) VALUES ($1, $2, now() + $3::interval)",
        code,
        username,
        ttl,
    )
    return code


async def consume_link_code(conn: asyncpg.Connection, code: str) -> str | None:
    """Код одноразовый: удаляется при первом же использовании, просроченный не срабатывает."""
    return await conn.fetchval(
        "DELETE FROM alerts.telegram_codes WHERE code = $1 RETURNING CASE WHEN expires_at > now() THEN username END",
        code,
    )


async def link_chat(conn: asyncpg.Connection, username: str, chat_id: int, tg_username: str | None) -> None:
    await conn.execute("DELETE FROM alerts.telegram_links WHERE chat_id = $1 AND username <> $2", chat_id, username)
    await conn.execute(
        """
        INSERT INTO alerts.telegram_links (username, chat_id, tg_username) VALUES ($1, $2, $3)
        ON CONFLICT (username) DO UPDATE SET chat_id = EXCLUDED.chat_id, tg_username = EXCLUDED.tg_username,
                                             linked_at = now()
        """,
        username,
        chat_id,
        tg_username,
    )


async def unlink(conn: asyncpg.Connection, *, username: str | None = None, chat_id: int | None = None) -> str | None:
    return await conn.fetchval(
        "DELETE FROM alerts.telegram_links WHERE username = $1 OR chat_id = $2 RETURNING username",
        username,
        chat_id,
    )


async def link_of_user(conn: asyncpg.Connection, username: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "SELECT chat_id, tg_username, linked_at FROM alerts.telegram_links WHERE username = $1", username
    )
    return None if row is None else dict(row)


async def user_of_chat(conn: asyncpg.Connection, chat_id: int) -> str | None:
    return await conn.fetchval("SELECT username FROM alerts.telegram_links WHERE chat_id = $1", chat_id)


async def chats_by_user(conn: asyncpg.Connection) -> dict[str, int]:
    rows = await conn.fetch("SELECT username, chat_id FROM alerts.telegram_links")
    return {r["username"]: int(r["chat_id"]) for r in rows}


async def get_offset(conn: asyncpg.Connection) -> int:
    return int(await conn.fetchval("SELECT update_offset FROM alerts.telegram_state WHERE id = 1") or 0)


async def set_offset(conn: asyncpg.Connection, offset: int) -> None:
    await conn.execute(
        "INSERT INTO alerts.telegram_state (id, update_offset) VALUES (1, $1) "
        "ON CONFLICT (id) DO UPDATE SET update_offset = EXCLUDED.update_offset",
        offset,
    )
