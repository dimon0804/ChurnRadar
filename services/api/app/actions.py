"""Работа со списком «С кем связаться сегодня»: очередь, отметки, выгрузка CSV.

Очередь — клиенты с оценкой риска (не unknown) и ненулевой ожидаемой потерей, по убыванию
ожидаемой потери: первым берём того, на ком можно спасти больше денег.

Жизненный цикл клиента в очереди:
  • в очереди — отметки нет;
  • «Письмо отправлено» (emailed) — ждём ответа FOLLOWUP дней. Если за это время риск вырос,
    клиент возвращается сразу («Риск вырос»), если срок прошёл — тоже возвращается
    («Ответа нет — напомнить»);
  • «Решено» (resolved) — клиент ответил или вопрос закрыт. Вернётся, только если риск вырастет;
  • «Отложено» (snoozed) — до выбранной даты, что бы ни случилось.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, model_validator
from svckit.errors import ApiError

from app.queries import (
    BIGINT_MAX,
    DEADLINE_SQL,
    MAIN_REASON_CODE_SQL,
    MAIN_REASON_SQL,
    NEXT_ACTION_SQL,
    URGENT_SQL,
    deadline_fields,
    iso_z,
    order_by,
    where_clause,
)

LEVEL_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
LEVEL_TITLE = {
    "high": "Высокий",
    "medium": "Средний",
    "low": "Низкий",
    "unknown": "Нет оценки",
}
STATUSES = ("emailed", "resolved", "snoozed")
MAX_SNOOZE_DAYS = 365
DEFAULT_FOLLOWUP_DAYS = 7
HISTORY_LIMIT = 20
EXPORT_MAX_ROWS = 100_000


class ActionUpdate(BaseModel):
    """Тело PUT /customers/{id}/action.

    snoozed — ровно одно из snooze_days и snoozed_until; emailed — можно указать срок ожидания
    ответа (snooze_days), иначе берётся настройка; resolved — без срока.
    """

    model_config = ConfigDict(extra="forbid")
    status: Literal["emailed", "resolved", "snoozed"]
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
        if self.status == "emailed" and given > 1:
            raise ValueError(
                "Для «Письмо отправлено» укажите snooze_days или snoozed_until, не оба"
            )
        if self.status == "resolved" and given:
            raise ValueError(
                "Для «Решено» срок не нужен: snooze_days и snoozed_until не указывайте"
            )
        if self.snoozed_until is not None and self.snoozed_until.tzinfo is None:
            raise ValueError(
                "snoozed_until — дата и время с часовым поясом, например 2026-11-01T09:00:00Z"
            )
        return self


def _invalid_until(message: str, detail: str, kind: str) -> ApiError:
    return ApiError(
        422,
        "validation_error",
        message,
        [{"field": "snoozed_until", "message": detail, "type": kind}],
    )


def snooze_deadline(
    update: ActionUpdate, now: datetime, followup_days: int = DEFAULT_FOLLOWUP_DAYS
) -> datetime | None:
    """До какого момента клиент не показывается в очереди. Срок в прошлом или дальше года — 422."""
    if update.status == "resolved":
        return None
    if update.snooze_days:
        until = now + timedelta(days=update.snooze_days)
    elif update.snoozed_until is not None:
        until = update.snoozed_until
    else:  # emailed без срока — ждём ответа по настройке
        until = now + timedelta(days=followup_days)
    if until <= now:
        raise _invalid_until("Срок должен быть в будущем", "Дата уже прошла", "in_past")
    if until > now + timedelta(days=MAX_SNOOZE_DAYS):
        raise _invalid_until(
            f"Срок не может быть дальше чем через {MAX_SNOOZE_DAYS} дней",
            "Слишком далёкая дата",
            "out_of_range",
        )
    return until


def risk_grew(
    loss_at_mark: int, level_at_mark: str, loss_now: int | None, level_now: str | None
) -> bool:
    if loss_now is None or level_now is None:
        return False
    return loss_now > loss_at_mark or LEVEL_RANK.get(level_now, 0) > LEVEL_RANK.get(
        level_at_mark, 0
    )


def return_reason(
    status: str,
    until: datetime | None,
    loss_at_mark: int,
    level_at_mark: str,
    loss_now: int | None,
    level_now: str | None,
    now: datetime,
) -> str | None:
    """Почему отметка больше не прячет клиента: risk_grew, no_reply, snooze_over. None — прячет."""
    if status == "snoozed":
        return None if until is not None and until > now else "snooze_over"
    if risk_grew(loss_at_mark, level_at_mark, loss_now, level_now):
        return "risk_grew"
    if status == "emailed" and (until is None or until <= now):
        return "no_reply"
    return None


def action_out(
    row: asyncpg.Record | dict[str, Any], now: datetime
) -> dict[str, Any] | None:
    """Отметка для API. row — колонки customer_actions (a_*) плюс текущие потеря и уровень клиента."""
    status = row["a_status"]
    if status is None:
        return None
    until = row["a_until"]
    reason = return_reason(
        status,
        until,
        row["a_loss"],
        row["a_level"],
        row["expected_loss"],
        row["risk_level"],
        now,
    )
    return {
        "status": status,
        "until": iso_z(until),
        "note": row["a_note"],
        "updated_at": iso_z(row["a_at"]),
        "updated_by": row["a_by"],
        "expected_loss_at_mark": row["a_loss"],
        "risk_level_at_mark": row["a_level"],
        "active": reason is None,
        "return_reason": reason,
    }


def _date(value: str | None) -> str:
    if not value:
        return ""
    return f"{datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC):%d.%m.%Y}"


def status_text(action: dict[str, Any] | None) -> str:
    """Статус одной строкой для CSV."""
    if action is None:
        return ""
    reason = action["return_reason"]
    if reason == "risk_grew":
        return "Риск вырос после отметки — снова в списке"
    if reason == "no_reply":
        return f"Письмо от {_date(action['updated_at'])}, ответа нет — напомнить"
    if reason == "snooze_over":
        return ""  # срок отложки прошёл: клиент снова в очереди
    if action["status"] == "emailed":
        return f"Письмо отправлено {_date(action['updated_at'])}"
    if action["status"] == "resolved":
        return "Решено"
    return f"Отложено до {_date(action['until'])}"


# --- SQL -------------------------------------------------------------------------------

ACTION_COLUMNS = (
    "a.status AS a_status, a.snoozed_until AS a_until, a.note AS a_note, a.updated_at AS a_at, "
    "a.updated_by AS a_by, a.expected_loss_at_mark AS a_loss, a.risk_level_at_mark AS a_level"
)

_RANK = (
    "(CASE {0} WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END)"
)
_NOT_GROWN = (
    f"cs.expected_loss <= a.expected_loss_at_mark AND {_RANK.format('cs.risk_level')} <= "
    f"{_RANK.format('a.risk_level_at_mark')}"
)
# отметка прячет клиента из очереди — те же правила, что в return_reason
HIDDEN_SQL = (
    f"(a.status = 'resolved' AND {_NOT_GROWN})"
    f" OR (a.status = 'emailed' AND a.snoozed_until > now() AND {_NOT_GROWN})"
    " OR (a.status = 'snoozed' AND a.snoozed_until > now())"
)
CANDIDATE_SQL = "cs.risk_level <> 'unknown' AND cs.expected_loss > 0"

_LIST_COLUMNS = (
    "cs.customer_id, cs.email, cs.risk_level, cs.risk_score, cs.expected_loss, "
    f"{MAIN_REASON_SQL} AS main_reason, {MAIN_REASON_CODE_SQL} AS main_reason_code, "
    f"{NEXT_ACTION_SQL} AS next_action, {DEADLINE_SQL} AS deadline_days"
)
# Срочных поднимаем наверх очереди, дальше — по деньгам, которые можно спасти.
QUEUE_ORDER = f"{URGENT_SQL} DESC, cs.expected_loss DESC, cs.customer_id ASC"
# Массовая рассылка по умолчанию — только высокий и средний риск: низкому писать незачем.
DEFAULT_CAMPAIGN_LEVELS = ("high", "medium")
QUEUE_LEVELS = ("high", "medium", "low")


def queue_item(row: asyncpg.Record, now: datetime) -> dict[str, Any]:
    return {
        "id": row["customer_id"],
        "email": row["email"],
        "risk_level": row["risk_level"],
        "risk_score": row["risk_score"],
        "expected_loss": row["expected_loss"],
        "main_reason": row["main_reason"],
        "main_reason_code": row["main_reason_code"],
        "next_action": row["next_action"],
        **deadline_fields(row.get("deadline_days")),
        "action": action_out(row, now),
    }


def queue_condition(marked: bool) -> str:
    hidden = f"({HIDDEN_SQL})"
    return f"{CANDIDATE_SQL} AND " + (
        f"a.status IS NOT NULL AND {hidden}"
        if marked
        else f"NOT COALESCE({hidden}, false)"
    )


async def contact_today(
    pool: asyncpg.Pool, *, limit: int, marked: bool
) -> dict[str, Any]:
    """Очередь «С кем связаться» или (marked=true) те, кого сейчас прячут отметки."""
    hidden = f"({HIDDEN_SQL})"
    async with pool.acquire() as conn:
        now = await conn.fetchval("SELECT now()")
        rows = await conn.fetch(
            f"SELECT {_LIST_COLUMNS}, {ACTION_COLUMNS} FROM scoring.customer_scores cs "
            f"LEFT JOIN scoring.customer_actions a ON a.customer_id = cs.customer_id "
            f"WHERE {queue_condition(marked)} ORDER BY {QUEUE_ORDER} LIMIT $1",
            limit,
        )
        visible = f"NOT COALESCE({hidden}, false)"
        counts = await conn.fetchrow(
            f"SELECT count(*) FILTER (WHERE {visible}) AS queue, "
            f"count(*) FILTER (WHERE a.status IS NOT NULL AND {hidden}) AS marked, "
            f"COALESCE(sum(cs.expected_loss) FILTER (WHERE {visible}), 0) AS queue_loss, "
            f"count(*) FILTER (WHERE {visible} AND cs.risk_level = 'high') AS q_high, "
            f"count(*) FILTER (WHERE {visible} AND cs.risk_level = 'medium') AS q_medium, "
            f"count(*) FILTER (WHERE {visible} AND cs.risk_level = 'low') AS q_low, "
            f"count(*) FILTER (WHERE {visible} AND {URGENT_SQL}) AS q_urgent "
            "FROM scoring.customer_scores cs LEFT JOIN scoring.customer_actions a ON a.customer_id = cs.customer_id "
            f"WHERE {CANDIDATE_SQL}"
        )
    return {
        "items": [queue_item(r, now) for r in rows],
        "queue_total": counts["queue"],
        "queue_expected_loss": int(counts["queue_loss"]),
        "marked_total": counts["marked"],
        "queue_by_level": {level: counts[f"q_{level}"] for level in QUEUE_LEVELS},
        "urgent_total": counts["q_urgent"],
    }


async def queue_ids(
    conn: asyncpg.Connection, limit: int, levels: list[str] | tuple[str, ...] = DEFAULT_CAMPAIGN_LEVELS
) -> list[int]:
    """Очередь (без отмеченных) выбранных уровней риска в том же порядке, что на обзоре, —
    для рассылки «всем в очереди»."""
    rows = await conn.fetch(
        "SELECT cs.customer_id FROM scoring.customer_scores cs "
        "LEFT JOIN scoring.customer_actions a ON a.customer_id = cs.customer_id "
        f"WHERE {queue_condition(False)} AND cs.risk_level = ANY($2::text[]) "
        f"ORDER BY {QUEUE_ORDER} LIMIT $1",
        limit,
        list(levels),
    )
    return [r["customer_id"] for r in rows]


def check_id(customer_id: int) -> None:
    if customer_id < 1 or customer_id > BIGINT_MAX:
        raise ApiError(404, "not_found", "Клиент не найден")


_STATE_SQL = (
    f"SELECT cs.expected_loss, cs.risk_level, {ACTION_COLUMNS} FROM scoring.customer_scores cs "
    "LEFT JOIN scoring.customer_actions a ON a.customer_id = cs.customer_id WHERE cs.customer_id = $1"
)


async def state(conn: asyncpg.Connection, customer_id: int) -> dict[str, Any]:
    now = await conn.fetchval("SELECT now()")
    row = await conn.fetchrow(_STATE_SQL, customer_id)
    if row is None:
        raise ApiError(
            404, "not_found", f"Клиент {customer_id} не найден или ещё не оценён"
        )
    history = await conn.fetch(
        "SELECT status, snoozed_until, note, expected_loss, email_id, changed_at, changed_by "
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
                "until": iso_z(h["snoozed_until"]),
                "note": h["note"],
                "expected_loss": h["expected_loss"],
                "email_id": h["email_id"],
                "changed_at": iso_z(h["changed_at"]),
                "changed_by": h["changed_by"],
            }
            for h in history
        ],
    }


async def get_action(pool: asyncpg.Pool, customer_id: int) -> dict[str, Any]:
    check_id(customer_id)
    async with pool.acquire() as conn:
        return await state(conn, customer_id)


async def record_action(
    conn: asyncpg.Connection,
    customer_id: int,
    status: str,
    until: datetime | None,
    note: str | None,
    user_name: str | None,
    email_id: int | None = None,
) -> bool:
    """Ставит отметку и пишет журнал в текущей транзакции. False — клиента нет в оценках."""
    current = await conn.fetchrow(
        "SELECT expected_loss, risk_level FROM scoring.customer_scores WHERE customer_id = $1",
        customer_id,
    )
    if current is None:
        return False
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
        status,
        until,
        note,
        current["expected_loss"],
        current["risk_level"],
        user_name,
    )
    await conn.execute(
        "INSERT INTO scoring.customer_actions_log "
        "(customer_id, status, snoozed_until, note, expected_loss, email_id, changed_by) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        customer_id,
        status,
        until,
        note,
        current["expected_loss"],
        email_id,
        user_name,
    )
    return True


async def set_action(
    pool: asyncpg.Pool,
    customer_id: int,
    update: ActionUpdate,
    user_name: str | None,
    followup_days: int = DEFAULT_FOLLOWUP_DAYS,
) -> dict[str, Any]:
    check_id(customer_id)
    async with pool.acquire() as conn, conn.transaction():
        now = await conn.fetchval("SELECT now()")
        until = snooze_deadline(update, now, followup_days)
        note = (update.note or "").strip() or None
        if not await record_action(
            conn, customer_id, update.status, until, note, user_name
        ):
            raise ApiError(
                404, "not_found", f"Клиент {customer_id} не найден или ещё не оценён"
            )
        return await state(conn, customer_id)


async def clear_action(
    pool: asyncpg.Pool, customer_id: int, user_name: str | None
) -> dict[str, Any]:
    check_id(customer_id)
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
        return await state(conn, customer_id)


# --- CSV -------------------------------------------------------------------------------

CSV_HEADER = [
    "ID",
    "Email",
    "Уровень риска",
    "Риск, баллы",
    "Ожидаемая потеря, €",
    "Баланс, €",
    "Пополнения всего, €",
    "Срочно",
    "Главная причина риска",
    "Самое выгодное действие",
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
        "да" if deadline_fields(row.get("deadline_days"))["urgent"] else "",
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
    reason: str | None = None,
    reason_scope: str = "any",
    score_min: float | None = None,
    score_max: float | None = None,
) -> list[list[str]]:
    where, args = where_clause(
        levels, search, reason=reason, reason_scope=reason_scope, score_min=score_min, score_max=score_max
    )
    outer = ""
    if worklist:
        outer = f" WHERE {CANDIDATE_SQL} AND NOT COALESCE(({HIDDEN_SQL}), false)"
    # where_clause и order_by пишут имена колонок без алиаса — фильтруем во вложенном запросе
    sql = (
        "SELECT cs.*, " + ACTION_COLUMNS + " FROM ("
        "SELECT customer_id, email, balance, total_replenished, risk_score, risk_level, expected_loss, "
        f"{MAIN_REASON_SQL} AS main_reason, {NEXT_ACTION_SQL} AS next_action, {DEADLINE_SQL} AS deadline_days "
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
