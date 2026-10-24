"""Пул соединений asyncpg."""

from __future__ import annotations

import asyncio
import json
import time

import asyncpg
import structlog

log = structlog.get_logger()


async def _init_connection(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str, min_size: int, max_size: int, timeout_s: float = 60.0) -> asyncpg.Pool:
    deadline = time.monotonic() + timeout_s
    delay = 0.5
    while True:
        try:
            return await asyncpg.create_pool(
                dsn, min_size=min_size, max_size=max_size, init=_init_connection, command_timeout=60
            )
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            if time.monotonic() >= deadline:
                raise
            log.warning("db_connect_failed", error=str(exc), retry_in=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)
