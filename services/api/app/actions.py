"""Работа со списком «Кому позвонить сегодня»: очередь, отметки «Сделано»/«Отложить», выгрузка CSV.

Очередь — клиенты с оценкой риска (не unknown) и ненулевой ожидаемой потерей, по убыванию
ожидаемой потери: первым берём того, на ком можно спасти больше денег. Отмеченные пропадают:
«Сделано» — пока у клиента не выросли потеря или уровень риска, «Отложить» — до даты.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, model_validator
from svckit.errors import ApiError

from app.queries import BIGINT_MAX, iso_z, order_by, where_clause

LEVEL_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
LEVEL_TITLE = {
    "high": "Высокий",
    "medium": "Средний",
    "low": "Низкий",
    "unknown": "Нет оценки",
}
STATUS_TITLE = {"done": "Сделано", "snoozed": "Отложено"}
MAX_SNOOZE_DAYS = 365
HISTORY_LIMIT = 20
EXPORT_MAX_ROWS = 100_000


class ActionUpdate(BaseModel):
    """Тело PUT /customers/{id}/action. Для «Отложить» — ровно одно из snooze_days и snoozed_until."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["done", "snoozed"]
    snooze_days: int | None = Field(default=None, ge=1, le=MAX_SNOOZE_DAYS)
    snoozed_until: datetime | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _snooze_fields(self) -> ActionUpdate:
        given = (self.snooze_days is not None) + (self.snoozed_until is not None)
        if self.status == "snoozed" and given != 1:
            raise ValueError(
                "Чтобы отложить, укажите snooze_days или snoozed_until (что-то одно)"
            )
        if self.status == "done" and given:
            raise ValueError(
                "Для «Сделано» срок не нужен: snooze_days и snoozed_until не указывайте"
            )
        if self.snoozed_until is not None and self.snoozed_until.tzinfo is None:
            raise ValueError(
                "snoozed_until — дата и время с часовым поясом, например 2026-11-01T09:00:00Z"
            )
        return self


def snooze_deadline(update: ActionUpdate, now: datetime) -> datetime | None:
    """До какого момента клиент отложен. Срок в прошлом или дальше года — 422."""
    if update.status != "snoozed":
        return None
    until = (
        now + timedelta(days=update.snooze_days)
        if update.snooze_days
        else update.snoozed_until
    )
    assert until is not None
    if until <= now:
        raise ApiError(
            422,
            "validation_error",
            "Отложить можно только на будущее",
            [
                {
                    "field": "snoozed_until",
                    "message": "Дата уже прошла",
                    "type": "in_past",
                }
            ],
        )
    if until > now + timedelta(days=MAX_SNOOZE_DAYS):
        raise ApiError(
            422,
            "validation_error",
            f"Отложить можно не больше чем на {MAX_SNOOZE_DAYS} дней",
            [
                {
                    "field": "snoozed_until",
                    "message": "Слишком далёкая дата",
                    "type": "out_of_range",
                }
            ],
        )
    return until


def is_returned(
    status: str,
    loss_at_mark: int,
    level_at_mark: str,
    loss_now: int | None,
    level_now: str | None,
) -> bool:
    """После «Сделано» потеря или уровень риска выросли — клиента снова надо брать в работу."""
    if status != "done" or loss_now is None or level_now is None:
        return False
    return loss_now > loss_at_mark or LEVEL_RANK.get(level_now, 0) > LEVEL_RANK.get(
        level_at_mark, 0
    )


def action_out(
    row: asyncpg.Record | dict[str, Any], now: datetime
) -> dict[str, Any] | None:
    """Отметка для API. row — колонки customer_actions (a_*) плюс текущие потеря и уровень клиента."""
    status = row["a_status"]
    if status is None:
        return None
    returned = is_returned(
        status, row["a_loss"], row["a_level"], row["expected_loss"], row["risk_level"]
    )
    until = row["a_until"]
    if status == "done":
        active = not returned
    else:
        active = until is not None and until > now
    return {
        "status": status,
        "snoozed_until": iso_z(until),
        "note": row["a_note"],
        "updated_at": iso_z(row["a_at"]),
        "updated_by": row["a_by"],
        "expected_loss_at_mark": row["a_loss"],
        "risk_level_at_mark": row["a_level"],
        "active": active,
        "returned": returned,
    }


def status_text(action: dict[str, Any] | None) -> str:
    """Статус одной строкой для CSV."""
    if action is None:
        return ""
    if action["status"] == "done":
        if action["returned"]:
            return "Сделано, но риск вырос — снова в списке"
        return "Сделано"
    if not action["active"]:
        return ""  # срок отложки прошёл: клиент снова в очереди
    until = datetime.fromisoformat(action["snoozed_until"].replace("Z", "+00:00"))
    return f"Отложено до {until.astimezone(UTC):%d.%m.%Y}"


# --- SQL -------------------------------------------------------------------------------

ACTION_COLUMNS = (
    "a.status AS a_status, a.snoozed_until AS a_until, a.note AS a_note, a.updated_at AS a_at, "
    "a.updated_by AS a_by, a.expected_loss_at_mark AS a_loss, a.risk_level_at_mark AS a_level"
)

# отметка прячет клиента из очереди: «Сделано» без роста риска или ещё не истёкшая отложка
HIDDEN_SQL = (
    "(a.status = 'done' AND cs.expected_loss <= a.expected_loss_at_mark AND "
    "(CASE cs.risk_level WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END) <= "
    "(CASE a.risk_level_at_mark WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END))"
    " OR (a.status = 'snoozed' AND a.snoozed_until > now())"
)
CANDIDATE_SQL = "cs.risk_level <> 'unknown' AND cs.expected_loss > 0"

_LIST_COLUMNS = (
    "cs.customer_id, cs.email, cs.risk_level, cs.risk_score, cs.expected_loss, "
    "cs.reasons -> 0 ->> 'title' AS main_reason, "
    "COALESCE(cs.detail -> 'next_action', cs.detail -> 'recommendations' -> 0) AS next_action"
)


def call_item(row: asyncpg.Record, now: datetime) -> dict[str, Any]:
    return {
        "id": row["customer_id"],
        "email": row["email"],
        "risk_level": row["risk_level"],
        "risk_score": row["risk_score"],
        "expected_loss": row["expected_loss"],
        "main_reason": row["main_reason"],
        "next_action": row["next_action"],
        "action": action_out(row, now),
    }


async def call_today(pool: asyncpg.Pool, *, limit: int, marked: bool) -> dict[str, Any]:
    """Очередь звонков или (marked=true) те, кого сейчас прячут отметки."""
    hidden = f"({HIDDEN_SQL})"
    cond = f"{CANDIDATE_SQL} AND " + (
        f"a.status IS NOT NULL AND {hidden}"
        if marked
        else f"NOT COALESCE({hidden}, false)"
    )
    async with pool.acquire() as conn:
        now = await conn.fetchval("SELECT now()")
        rows = await conn.fetch(
            f"SELECT {_LIST_COLUMNS}, {ACTION_COLUMNS} FROM scoring.customer_scores cs "
            f"LEFT JOIN scoring.customer_actions a ON a.customer_id = cs.customer_id "
            f"WHERE {cond} ORDER BY cs.expected_loss DESC, cs.customer_id ASC LIMIT $1",
            limit,
        )
        counts = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE NOT COALESCE("
            + hidden
            + ", false)) AS queue, "
            "count(*) FILTER (WHERE a.status IS NOT NULL AND "
            + hidden
            + ") AS marked, "
            "COALESCE(sum(cs.expected_loss) FILTER (WHERE NOT COALESCE("
            + hidden
            + ", false)), 0) AS queue_loss "
            "FROM scoring.customer_scores cs LEFT JOIN scoring.customer_actions a ON a.customer_id = cs.customer_id "
            f"WHERE {CANDIDATE_SQL}"
        )
    return {
        "items": [call_item(r, now) for r in rows],
        "queue_total": counts["queue"],
        "queue_expected_loss": int(counts["queue_loss"]),
        "marked_total": counts["marked"],
    }


def _check_id(customer_id: int) -> None:
    if customer_id < 1 or customer_id > BIGINT_MAX:
        raise ApiError(404, "not_found", "Клиент не найден")


_STATE_SQL = (
    f"SELECT cs.expected_loss, cs.risk_level, {ACTION_COLUMNS} FROM scoring.customer_scores cs "
    "LEFT JOIN scoring.customer_actions a ON a.customer_id = cs.customer_id WHERE cs.customer_id = $1"
)


async def _state(conn: asyncpg.Connection, customer_id: int) -> dict[str, Any]:
    now = await conn.fetchval("SELECT now()")
    row = await conn.fetchrow(_STATE_SQL, customer_id)
    if row is None:
        raise ApiError(
            404, "not_found", f"Клиент {customer_id} не найден или ещё не оценён"
        )
    history = await conn.fetch(
        "SELECT status, snoozed_until, note, expected_loss, changed_at, changed_by "
        "FROM scoring.customer_actions_log WHERE customer_id = $1 ORDER BY id DESC LIMIT $2",
        customer_id,
        HISTORY_LIMIT,
    )
    return {
        "customer_id": customer_id,
        "action": action_out(row, now),
        "history": [
            {
                "status": h["status"],
                "snoozed_until": iso_z(h["snoozed_until"]),
                "note": h["note"],
                "expected_loss": h["expected_loss"],
                "changed_at": iso_z(h["changed_at"]),
                "changed_by": h["changed_by"],
            }
            for h in history
        ],
    }


async def get_action(pool: asyncpg.Pool, customer_id: int) -> dict[str, Any]:
    _check_id(customer_id)
    async with pool.acquire() as conn:
        return await _state(conn, customer_id)


async def set_action(
    pool: asyncpg.Pool, customer_id: int, update: ActionUpdate, user_name: str | None
) -> dict[str, Any]:
    _check_id(customer_id)
    async with pool.acquire() as conn, conn.transaction():
        now = await conn.fetchval("SELECT now()")
        until = snooze_deadline(update, now)
        current = await conn.fetchrow(
            "SELECT expected_loss, risk_level FROM scoring.customer_scores WHERE customer_id = $1",
            customer_id,
        )
        if current is None:
            raise ApiError(
                404, "not_found", f"Клиент {customer_id} не найден или ещё не оценён"
            )
        note = (update.note or "").strip() or None
        await conn.execute(
            """
            INSERT INTO scoring.customer_actions (customer_id, status, snoozed_until, note,
                expected_loss_at_mark, risk_level_at_mark, updated_at, updated_by)
            VALUES ($1, $2, $3, $4, $5, $6, now(), $7)
            ON CONFLICT (customer_id) DO UPDATE SET status = EXCLUDED.status,
                snoozed_until = EXCLUDED.snoozed_until, note = EXCLUDED.note,
                expected_loss_at_mark = EXCLUDED.expected_loss_at_mark,
                risk_level_at_mark = EXCLUDED.risk_level_at_mark,
                updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by
            """,
            customer_id,
            update.status,
            until,
            note,
            current["expected_loss"],
            current["risk_level"],
            user_name,
        )
        await conn.execute(
            "INSERT INTO scoring.customer_actions_log (customer_id, status, snoozed_until, note, expected_loss, changed_by) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            customer_id,
            update.status,
            until,
            note,
            current["expected_loss"],
            user_name,
        )
        return await _state(conn, customer_id)


async def clear_action(
    pool: asyncpg.Pool, customer_id: int, user_name: str | None
) -> dict[str, Any]:
    _check_id(customer_id)
    async with pool.acquire() as conn, conn.transaction():
        deleted = await conn.fetchrow(
            "DELETE FROM scoring.customer_actions WHERE customer_id = $1 RETURNING customer_id",
            customer_id,
        )
        if deleted is not None:
            await conn.execute(
                "INSERT INTO scoring.customer_actions_log (customer_id, status, expected_loss, changed_by) "
                "SELECT $1, 'cleared', (SELECT expected_loss FROM scoring.customer_scores WHERE customer_id = $1), $2",
                customer_id,
                user_name,
            )
        return await _state(conn, customer_id)


# --- CSV -------------------------------------------------------------------------------

CSV_HEADER = [
    "ID",
    "Email",
    "Уровень риска",
    "Риск, баллы",
    "Ожидаемая потеря, €",
    "Баланс, €",
    "Пополнения всего, €",
    "Главная причина",
    "Что сделать",
    "Статус",
]
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def euros(cents: int | None) -> str:
    """Евроценты -> «1385,00»: запятая, чтобы русский Excel понял число."""
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    whole, rest = divmod(abs(int(cents)), 100)
    return f"{sign}{whole},{rest:02d}"


def safe_text(value: str | None) -> str:
    """Текст, который Excel не примет за формулу (CSV injection)."""
    text = (value or "").replace("\r", " ").replace("\n", " ")
    return "'" + text if text.startswith(_FORMULA_START) else text


def csv_row(row: asyncpg.Record | dict[str, Any], now: datetime) -> list[str]:
    action = action_out(row, now)
    next_action = row["next_action"] or {}
    score = row["risk_score"]
    return [
        str(row["customer_id"]),
        safe_text(row["email"]),
        LEVEL_TITLE.get(row["risk_level"], row["risk_level"]),
        "" if score is None else f"{score:.1f}".replace(".", ","),
        euros(row["expected_loss"]),
        euros(row["balance"]),
        euros(row["total_replenished"]),
        safe_text(row["main_reason"]),
        safe_text(next_action.get("title") if isinstance(next_action, dict) else None),
        status_text(action),
    ]


def render_csv(rows: list[list[str]]) -> bytes:
    """UTF-8 с BOM и «;» — так файл без мастера импорта открывается в русском Excel."""
    buf = io.StringIO()
    writer = csv.writer(
        buf, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL
    )
    writer.writerow(CSV_HEADER)
    writer.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")


async def export_rows(
    pool: asyncpg.Pool,
    *,
    sort: str,
    order: str,
    levels: list[str],
    search: str | None,
    worklist: bool,
) -> list[list[str]]:
    where, args = where_clause(levels, search)
    outer = ""
    if worklist:
        outer = f" WHERE {CANDIDATE_SQL} AND NOT COALESCE(({HIDDEN_SQL}), false)"
    # where_clause и order_by пишут имена колонок без алиаса — фильтруем во вложенном запросе
    sql = (
        "SELECT cs.*, " + ACTION_COLUMNS + " FROM ("
        "SELECT customer_id, email, balance, total_replenished, risk_score, risk_level, expected_loss, "
        "reasons -> 0 ->> 'title' AS main_reason, "
        "COALESCE(detail -> 'next_action', detail -> 'recommendations' -> 0) AS next_action "
        f"FROM scoring.customer_scores{where}) cs "
        "LEFT JOIN scoring.customer_actions a ON a.customer_id = cs.customer_id"
        f"{outer} ORDER BY {_qualified(order_by(sort, order))} LIMIT {EXPORT_MAX_ROWS}"
    )
    async with pool.acquire() as conn:
        now = await conn.fetchval("SELECT now()")
        rows = await conn.fetch(sql, *args)
    return [csv_row(r, now) for r in rows]


def _qualified(order_sql: str) -> str:
    """ORDER BY рейтинга с алиасом cs: после JOIN customer_id есть в обеих таблицах."""
    return ", ".join(f"cs.{part.strip()}" for part in order_sql.split(","))


def export_filename(now: datetime) -> str:
    return f"churnradar-clients-{now.astimezone(UTC):%Y-%m-%d}.csv"
