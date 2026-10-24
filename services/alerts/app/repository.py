"""Postgres: scoring.* только читаем, пишем только в схему alerts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import asyncpg
from svckit import db

from .rules import Alert, StateRow, TotalState

# Один обработчик пачки за раз на весь кластер: реплики не спорят о состоянии клиентов.
LOCK_KEY = 7_340_004


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
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


# --- состояние ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Meta:
    baseline_at: datetime | None
    total: TotalState | None


async def load_meta(conn: asyncpg.Connection) -> Meta:
    row = await conn.fetchrow(
        "SELECT baseline_at, total_loss, total_loss_ref, total_last_alert_at FROM alerts.meta WHERE id = 1"
    )
    if row is None:
        await conn.execute("INSERT INTO alerts.meta (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        return Meta(None, None)
    total = None
    if row["total_loss_ref"] is not None:
        total = TotalState(int(row["total_loss"]), int(row["total_loss_ref"]), row["total_last_alert_at"])
    return Meta(row["baseline_at"], total)


async def save_total(conn: asyncpg.Connection, total: TotalState) -> None:
    await conn.execute(
        "UPDATE alerts.meta SET total_loss = $1, total_loss_ref = $2, total_last_alert_at = $3, updated_at = now() "
        "WHERE id = 1",
        total.total_loss,
        total.total_loss_ref,
        total.total_last_alert_at,
    )


async def take_baseline(conn: asyncpg.Connection, now: datetime) -> int:
    """Первый запуск: запоминаем всех как есть и ничего не шлём, иначе первым же событием
    придут оповещения обо всех, кто уже в высоком риске."""
    await conn.execute("TRUNCATE alerts.customer_state")
    status = await conn.execute(
        "INSERT INTO alerts.customer_state (customer_id, risk_level, expected_loss, loss_ref) "
        "SELECT customer_id, risk_level, expected_loss, expected_loss FROM scoring.customer_scores"
    )
    await conn.execute("UPDATE alerts.meta SET baseline_at = $1, updated_at = now() WHERE id = 1", now)
    return int(status.split()[-1])


async def changed_rows(conn: asyncpg.Connection, customer_ids: Sequence[int] | None) -> list[dict[str, Any]]:
    """Клиенты, у которых уровень или ожидаемая потеря разошлись с запомненными, и новые клиенты.
    customer_ids=None — по всей таблице (полный пересчёт)."""
    rows = await conn.fetch(
        """
        SELECT s.customer_id, s.email, s.risk_level, s.expected_loss,
               st.risk_level AS prev_level, st.expected_loss AS prev_loss, st.loss_ref, st.last_alert_at
        FROM scoring.customer_scores s
        LEFT JOIN alerts.customer_state st ON st.customer_id = s.customer_id
        WHERE ($1::bigint[] IS NULL OR s.customer_id = ANY($1::bigint[]))
          AND (st.customer_id IS NULL
               OR st.risk_level IS DISTINCT FROM s.risk_level
               OR st.expected_loss IS DISTINCT FROM s.expected_loss)
        """,
        None if customer_ids is None else list(customer_ids),
    )
    return [dict(r) for r in rows]


async def forget_removed(conn: asyncpg.Connection, customer_ids: Sequence[int] | None) -> int:
    """Клиенты, которых больше нет в scoring.customer_scores, — из состояния."""
    status = await conn.execute(
        """
        DELETE FROM alerts.customer_state st
        WHERE ($1::bigint[] IS NULL OR st.customer_id = ANY($1::bigint[]))
          AND NOT EXISTS (SELECT 1 FROM scoring.customer_scores s WHERE s.customer_id = st.customer_id)
        """,
        None if customer_ids is None else list(customer_ids),
    )
    return int(status.split()[-1])


async def save_states(conn: asyncpg.Connection, states: Sequence[StateRow]) -> None:
    if not states:
        return
    await conn.execute(
        """
        INSERT INTO alerts.customer_state (customer_id, risk_level, expected_loss, loss_ref, last_alert_at, updated_at)
        SELECT u.customer_id, u.risk_level, u.expected_loss, u.loss_ref, u.last_alert_at, now()
        FROM unnest($1::bigint[], $2::text[], $3::bigint[], $4::bigint[], $5::timestamptz[])
             AS u(customer_id, risk_level, expected_loss, loss_ref, last_alert_at)
        ON CONFLICT (customer_id) DO UPDATE SET
            risk_level = EXCLUDED.risk_level, expected_loss = EXCLUDED.expected_loss,
            loss_ref = EXCLUDED.loss_ref, last_alert_at = EXCLUDED.last_alert_at, updated_at = now()
        """,
        [s.customer_id for s in states],
        [s.risk_level for s in states],
        [s.expected_loss for s in states],
        [s.loss_ref for s in states],
        [s.last_alert_at for s in states],
    )


async def count_high_state(conn: asyncpg.Connection) -> int:
    """Сколько клиентов было в высоком риске по запомненному состоянию (до обновления)."""
    return int(await conn.fetchval("SELECT count(*) FROM alerts.customer_state WHERE risk_level = 'high'"))


async def count_high_scores(conn: asyncpg.Connection) -> int:
    return int(await conn.fetchval("SELECT count(*) FROM scoring.customer_scores WHERE risk_level = 'high'"))


async def total_loss_from_summary(conn: asyncpg.Connection) -> int | None:
    """Если в событии нет summary.expected_loss — берём из сводки scoring."""
    value = await conn.fetchval("SELECT (data->>'expected_loss')::bigint FROM scoring.summary WHERE id = 1")
    return None if value is None else int(value)


# --- лента и доставка -----------------------------------------------------------------------------


async def insert_feed(
    conn: asyncpg.Connection, alerts: Sequence[Alert], *, batch_id: str, event_id: str, now: datetime
) -> None:
    if not alerts:
        return
    await conn.executemany(
        """
        INSERT INTO alerts.feed (batch_id, event_id, kind, customer_id, email, risk_level, prev_level,
                                 expected_loss, prev_loss, message, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        [
            (
                batch_id,
                event_id,
                a.kind,
                a.customer_id,
                a.email,
                a.risk_level,
                a.prev_level,
                a.expected_loss,
                a.prev_loss,
                a.message,
                now,
            )
            for a in alerts
        ],
    )


async def enqueue_delivery(conn: asyncpg.Connection, *, batch_id: str, channel: str, payload: dict[str, Any]) -> None:
    await conn.execute(
        "INSERT INTO alerts.deliveries (batch_id, channel, payload) VALUES ($1, $2, $3) "
        "ON CONFLICT (batch_id, channel) DO NOTHING",
        batch_id,
        channel,
        payload,
    )


@dataclass(frozen=True, slots=True)
class Delivery:
    id: int
    channel: str
    payload: dict[str, Any]
    attempts: int
    batch_id: str


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
            RETURNING d.id, d.channel, d.payload, d.attempts, d.batch_id::text
            """,
            limit,
            float(lease_s),
        )
    out = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append(Delivery(r["id"], r["channel"], payload, r["attempts"], r["batch_id"]))
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


async def prune(pool: asyncpg.Pool, retention: timedelta) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM alerts.feed WHERE created_at < now() - $1::interval", retention)
        await conn.execute(
            "DELETE FROM alerts.deliveries WHERE status <> 'pending' AND created_at < now() - $1::interval", retention
        )
        await conn.execute("DELETE FROM alerts.processed_events WHERE processed_at < now() - interval '2 days'")


# --- чтение ленты (HTTP) ----------------------------------------------------------------------------


async def list_feed(
    pool: asyncpg.Pool, *, page: int, page_size: int, customer_id: int | None, kind: str | None
) -> tuple[list[dict[str, Any]], int, int | None]:
    """Страница ленты, всего записей по фильтру и id самой свежей записи (для счётчика новых)."""
    where = "WHERE ($1::bigint IS NULL OR customer_id = $1) AND ($2::text IS NULL OR kind = $2)"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, kind, customer_id, email, risk_level, prev_level, expected_loss, prev_loss,
                   message, created_at
            FROM alerts.feed {where}
            ORDER BY id DESC
            LIMIT $3 OFFSET $4
            """,
            customer_id,
            kind,
            page_size,
            (page - 1) * page_size,
        )
        total = await conn.fetchval(f"SELECT count(*) FROM alerts.feed {where}", customer_id, kind)
        latest = await conn.fetchval("SELECT max(id) FROM alerts.feed")
    return [dict(r) for r in rows], int(total or 0), latest
