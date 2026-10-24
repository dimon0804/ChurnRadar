"""Пул соединений asyncpg и проверки БД для healthz."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import asyncpg
import structlog
from fastapi import Request

from .errors import ApiError

log = structlog.get_logger()


async def _init_json_codec(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


def session_settings(
    statement_timeout_ms: int | None, idle_in_transaction_timeout_ms: int | None
) -> dict[str, str]:
    """server_settings для asyncpg: таймауты в миллисекундах строками."""
    out: dict[str, str] = {}
    if statement_timeout_ms is not None:
        out["statement_timeout"] = str(int(statement_timeout_ms))
    if idle_in_transaction_timeout_ms is not None:
        out["idle_in_transaction_session_timeout"] = str(int(idle_in_transaction_timeout_ms))
    return out


async def create_pool(
    dsn: str,
    min_size: int = 1,
    max_size: int = 10,
    *,
    timeout_s: float = 60.0,
    command_timeout: float = 60.0,
    json_codec: bool = True,
    statement_timeout_ms: int | None = 60_000,
    idle_in_transaction_timeout_ms: int | None = 60_000,
) -> asyncpg.Pool:
    """Пул с повторами подключения в течение timeout_s (0 — без повторов).

    json_codec=True — json/jsonb отдаются и принимаются как объекты Python; без него —
    строками (так работает scoring, который сам сериализует jsonb).

    statement_timeout_ms и idle_in_transaction_timeout_ms ставятся на сессию: зависший запрос
    или забытая транзакция не держат блокировки вечно. None — оставить как на сервере, 0 — без лимита.
    """
    server_settings = session_settings(statement_timeout_ms, idle_in_transaction_timeout_ms)
    deadline = time.monotonic() + timeout_s
    delay = 0.5
    while True:
        try:
            return await asyncpg.create_pool(
                dsn,
                min_size=min_size,
                max_size=max_size,
                init=_init_json_codec if json_codec else None,
                command_timeout=command_timeout,
                server_settings=server_settings or None,
            )
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            if time.monotonic() >= deadline:
                raise
            log.warning("db_connect_failed", error=str(exc), retry_in=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)


def get_pool(request: Request) -> Any:
    """Зависимость FastAPI: пул из app.state.pool, без него — 503."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise ApiError(503, "service_unavailable", "База данных недоступна")
    return pool


async def db_ready(pool: Any, timeout_s: float = 2.0) -> bool:
    if pool is None:
        return False
    try:
        async with pool.acquire(timeout=timeout_s) as conn:
            return bool(await conn.fetchval("SELECT 1") == 1)
    except Exception:
        return False
