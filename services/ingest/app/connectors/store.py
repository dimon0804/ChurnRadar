"""Таблицы source.connectors и source.connector_runs: настройки, статус, курсор, история запусков."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import asyncpg

from .models import read_token, token_present

COLUMNS = (
    "id, kind, title, base_url, token_env, interval_s, enabled, managed, options, cursor, next_run_at, "
    "run_requested_at, enabled_at, last_run_at, last_success_at, last_status, last_error, last_duration_ms, "
    "last_records, consecutive_failures, created_at, updated_at, updated_by"
)
RUN_COLUMNS = "id, connector_id, trigger, status, started_at, finished_at, records, attempts, error, details"
# Сколько последних запусков храним на коннектор.
RUNS_KEEP = 200


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value: Any) -> Any:
    # пул ingest декодирует jsonb сам; соединение без кодека (планировщик) отдаёт строку
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


def to_api(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "base_url": row["base_url"],
        "interval_s": row["interval_s"],
        "enabled": row["enabled"],
        "token_env": row["token_env"],
        "token_present": token_present(row["token_env"]),
        "managed": row["managed"],
        "options": _json(row["options"]),
        "cursor": _json(row["cursor"]),
        "status": {
            "last_status": row["last_status"],
            "last_run_at": _iso(row["last_run_at"]),
            "last_success_at": _iso(row["last_success_at"]),
            "last_duration_ms": row["last_duration_ms"],
            "last_records": row["last_records"],
            "last_error": row["last_error"],
            "consecutive_failures": row["consecutive_failures"],
            "next_run_at": _iso(row["next_run_at"]) if row["enabled"] else None,
            "run_requested": row["run_requested_at"] is not None,
        },
    }


def run_to_api(row: asyncpg.Record) -> dict[str, Any]:
    finished = row["finished_at"]
    duration = None if finished is None else int((finished - row["started_at"]).total_seconds() * 1000)
    return {
        "id": row["id"],
        "connector_id": row["connector_id"],
        "trigger": row["trigger"],
        "status": row["status"],
        "started_at": _iso(row["started_at"]),
        "finished_at": _iso(finished),
        "duration_ms": duration,
        "records": row["records"],
        "attempts": row["attempts"],
        "error": row["error"],
        "details": _json(row["details"]),
    }


async def list_connectors(conn: asyncpg.Connection | asyncpg.Pool) -> list[asyncpg.Record]:
    return await conn.fetch(f"SELECT {COLUMNS} FROM source.connectors ORDER BY created_at, id")


async def get_connector(conn: asyncpg.Connection | asyncpg.Pool, connector_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(f"SELECT {COLUMNS} FROM source.connectors WHERE id = $1", connector_id)


async def create_connector(pool: asyncpg.Pool, data: dict[str, Any], user: str) -> asyncpg.Record | None:
    """None — такой id уже есть."""
    return await pool.fetchrow(
        f"""
        INSERT INTO source.connectors (id, kind, title, base_url, token_env, interval_s, enabled, options,
                                       next_run_at, enabled_at, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::text::jsonb,
                CASE WHEN $7 THEN now() END, CASE WHEN $7 THEN now() END, $9)
        ON CONFLICT (id) DO NOTHING
        RETURNING {COLUMNS}
        """,
        data["id"], data["kind"], data["title"], data["base_url"], data["token_env"], data["interval_s"],
        data["enabled"], json.dumps(data["options"]), user,
    )


async def update_connector(
    pool: asyncpg.Pool, connector_id: str, changes: dict[str, Any], user: str
) -> asyncpg.Record | None:
    """Частичное обновление. Включение планирует запуск сразу, смена интервала — от последнего запуска."""
    sets: dict[str, str] = {}
    args: list[Any] = [connector_id]

    def arg(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    for column in ("title", "base_url", "token_env", "options"):
        if column in changes:
            value = json.dumps(changes[column]) if column == "options" else changes[column]
            cast = "::text::jsonb" if column == "options" else ""
            sets[column] = f"{arg(value)}{cast}"
    if "interval_s" in changes:
        n = arg(changes["interval_s"])
        sets["interval_s"] = n
        sets["next_run_at"] = (
            "CASE WHEN next_run_at IS NULL THEN NULL "
            f"ELSE LEAST(next_run_at, coalesce(last_run_at, now()) + make_interval(secs => {n}::int)) END"
        )
    if "enabled" in changes:
        n = arg(changes["enabled"])
        sets["enabled_at"] = f"CASE WHEN {n} AND NOT enabled THEN now() ELSE enabled_at END"
        # включение планирует запуск сразу и важнее пересчёта по новому интервалу
        previous = sets.get("next_run_at", "next_run_at")
        sets["next_run_at"] = f"CASE WHEN {n} AND NOT enabled THEN now() ELSE {previous} END"
        sets["enabled"] = n
    sets["updated_by"] = arg(user)
    sets["updated_at"] = "now()"
    assignments = ", ".join(f"{column} = {value}" for column, value in sets.items())
    return await pool.fetchrow(
        f"UPDATE source.connectors SET {assignments} WHERE id = $1 RETURNING {COLUMNS}", *args
    )


async def delete_connector(pool: asyncpg.Pool, connector_id: str) -> bool:
    return (await pool.execute("DELETE FROM source.connectors WHERE id = $1", connector_id)) == "DELETE 1"


async def request_run(pool: asyncpg.Pool, connector_id: str) -> asyncpg.Record | None:
    return await pool.fetchrow(
        f"UPDATE source.connectors SET run_requested_at = coalesce(run_requested_at, now()) "
        f"WHERE id = $1 RETURNING {COLUMNS}",
        connector_id,
    )


async def reset_cursor(pool: asyncpg.Pool, connector_id: str, user: str) -> asyncpg.Record | None:
    return await pool.fetchrow(
        f"UPDATE source.connectors SET cursor = '{{}}'::jsonb, updated_at = now(), updated_by = $2 "
        f"WHERE id = $1 RETURNING {COLUMNS}",
        connector_id, user,
    )


async def reset_metrica_cursors(conn: asyncpg.Connection | asyncpg.Pool) -> None:
    """Связка визитов с аккаунтами поменялась — следующий запуск Метрики перечитает всю глубину."""
    await conn.execute("UPDATE source.connectors SET cursor = '{}'::jsonb WHERE kind = 'yandex_metrica'")


async def list_runs(pool: asyncpg.Pool, connector_id: str, limit: int) -> list[asyncpg.Record]:
    return await pool.fetch(
        f"SELECT {RUN_COLUMNS} FROM source.connector_runs WHERE connector_id = $1 "
        "ORDER BY started_at DESC, id DESC LIMIT $2",
        connector_id, limit,
    )


# --- исполнение ---------------------------------------------------------------------------


async def due_connectors(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT id FROM source.connectors
        WHERE run_requested_at IS NOT NULL
           OR (enabled AND (next_run_at IS NULL OR next_run_at <= now()))
        ORDER BY coalesce(run_requested_at, next_run_at, created_at), id
        """
    )
    return [r["id"] for r in rows]


async def start_run(conn: asyncpg.Connection, row: asyncpg.Record, instance: str) -> tuple[int, str]:
    trigger = "manual" if row["run_requested_at"] is not None else "schedule"
    async with conn.transaction():
        await conn.execute(
            "UPDATE source.connectors SET run_requested_at = NULL, last_status = 'running', last_run_at = now() "
            "WHERE id = $1",
            row["id"],
        )
        run_id = await conn.fetchval(
            "INSERT INTO source.connector_runs (connector_id, trigger, status, instance) "
            "VALUES ($1, $2, 'running', $3) RETURNING id",
            row["id"], trigger, instance,
        )
    return int(run_id), trigger


async def finish_ok(
    conn: asyncpg.Connection,
    connector_id: str,
    run_id: int,
    *,
    cursor: dict[str, Any],
    records: int,
    attempts: int,
    details: dict[str, Any],
    duration_ms: int,
    run_again: bool = False,
) -> None:
    """run_again — источник отдал не всё (упёрлись в max_records), следующий запуск сразу."""
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE source.connectors SET
                last_status = 'ok', last_error = NULL, last_success_at = now(), last_duration_ms = $2,
                last_records = $3, consecutive_failures = 0, cursor = $4::text::jsonb,
                next_run_at = CASE WHEN $5 THEN now() ELSE now() + make_interval(secs => interval_s) END
            WHERE id = $1
            """,
            connector_id, duration_ms, records, json.dumps(cursor), run_again,
        )
        await conn.execute(
            "UPDATE source.connector_runs SET status = 'ok', finished_at = now(), records = $2, attempts = $3, "
            "details = $4::text::jsonb WHERE id = $1",
            run_id, records, attempts, json.dumps(details, ensure_ascii=False),
        )
        await _prune_runs(conn, connector_id)


async def finish_error(
    conn: asyncpg.Connection,
    connector_id: str,
    run_id: int,
    *,
    error: str,
    attempts: int,
    duration_ms: int,
    backoff_base_s: float,
    details: dict[str, Any] | None = None,
) -> None:
    """Пауза до повтора растёт вдвое с каждой неудачей подряд, но не дольше интервала коннектора."""
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE source.connectors SET
                last_status = 'error', last_error = $2, last_duration_ms = $3, last_records = 0,
                consecutive_failures = consecutive_failures + 1,
                next_run_at = now() + make_interval(secs => LEAST(
                    interval_s::double precision,
                    $4::double precision * power(2, LEAST(consecutive_failures, 16))
                ))
            WHERE id = $1
            """,
            connector_id, error[:2000], duration_ms, backoff_base_s,
        )
        await conn.execute(
            "UPDATE source.connector_runs SET status = 'error', finished_at = now(), error = $2, attempts = $3, "
            "details = $4::text::jsonb WHERE id = $1",
            run_id, error[:2000], attempts, json.dumps(details or {}, ensure_ascii=False),
        )
        await _prune_runs(conn, connector_id)


async def _prune_runs(conn: asyncpg.Connection, connector_id: str) -> None:
    await conn.execute(
        """
        DELETE FROM source.connector_runs WHERE connector_id = $1 AND id < (
            SELECT min(id) FROM (
                SELECT id FROM source.connector_runs WHERE connector_id = $1 ORDER BY id DESC LIMIT $2
            ) keep
        )
        """,
        connector_id, RUNS_KEEP,
    )


async def abandoned_runs(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch("SELECT id, connector_id FROM source.connector_runs WHERE status = 'running'")


async def mark_abandoned(conn: asyncpg.Connection, run_id: int, connector_id: str) -> None:
    message = "Запуск прерван: исполнитель перезапустился или сменился"
    async with conn.transaction():
        await conn.execute(
            "UPDATE source.connector_runs SET status = 'error', finished_at = now(), error = $2 WHERE id = $1",
            run_id, message,
        )
        await conn.execute(
            "UPDATE source.connectors SET last_status = 'error', last_error = $2, "
            "consecutive_failures = consecutive_failures + 1, next_run_at = now() "
            "WHERE id = $1 AND last_status = 'running'",
            connector_id, message,
        )


async def gauge_rows(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT id, enabled, interval_s, coalesce(last_success_at, enabled_at, created_at) AS fresh_at "
        "FROM source.connectors"
    )


async def bootstrap(
    conn: asyncpg.Connection,
    *,
    connector_id: str,
    kind: str,
    title: str,
    base_url: str,
    token_env: str,
    interval_s: int,
    options: dict[str, Any],
) -> None:
    """Коннектор из окружения: адрес, токен и опции из env перезаписываются при каждом старте,
    включённость и расписание остаются такими, какими их сделали в интерфейсе."""
    await conn.execute(
        """
        INSERT INTO source.connectors (id, kind, title, base_url, token_env, interval_s, enabled, managed,
                                       options, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6, false, true, $7::text::jsonb, 'bootstrap')
        ON CONFLICT (id) DO UPDATE SET
            base_url = EXCLUDED.base_url, token_env = EXCLUDED.token_env, managed = true,
            options = source.connectors.options || EXCLUDED.options, updated_at = now()
        """,
        connector_id, kind, title, base_url, token_env, interval_s, json.dumps(options),
    )


async def unmanage(conn: asyncpg.Connection, connector_id: str) -> None:
    await conn.execute("UPDATE source.connectors SET managed = false WHERE id = $1 AND managed", connector_id)


def token_for(row: asyncpg.Record) -> str | None:
    return read_token(row["token_env"])


async def bootstrap_from_settings(conn: asyncpg.Connection, settings: Any) -> None:
    """Коннекторы billing и metrica из переменных окружения ingest (см. README)."""
    if settings.connector_billing_url:
        await bootstrap(
            conn, connector_id="billing", kind="billing_http", title="Биллинг (HTTP API)",
            base_url=settings.connector_billing_url, token_env="CONNECTOR_TOKEN_BILLING",
            interval_s=settings.connector_billing_interval_s, options={},
        )
    else:
        await unmanage(conn, "billing")
    if settings.connector_metrica_url:
        options = {"counter_id": settings.connector_metrica_counter} if settings.connector_metrica_counter else {}
        await bootstrap(
            conn, connector_id="metrica", kind="yandex_metrica", title="Яндекс Метрика",
            base_url=settings.connector_metrica_url, token_env="CONNECTOR_TOKEN_METRICA",
            interval_s=settings.connector_metrica_interval_s, options=options,
        )
    else:
        await unmanage(conn, "metrica")
