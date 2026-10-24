"""Применяет SQL-миграции из каталогов MIGRATIONS_DIRS по алфавиту.

Учёт ведётся в public.schema_migrations (имя записи — "<каталог>/<файл>").
Каждый файл выполняется в своей транзакции. Параллельный запуск нескольких
экземпляров безопасен: работа идёт под pg_advisory_lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import asyncpg

DEFAULT_DIRS = "/migrations/core,/migrations/auth"
LOCK_KEY = 7_340_211_001  # произвольная константа, общая для всех экземпляров мигратора

log = logging.getLogger("migrate")


@dataclass(frozen=True, slots=True)
class Migration:
    name: str
    path: Path

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def discover(dirs: list[str]) -> list[Migration]:
    """Список миграций: каталоги в заданном порядке, файлы в каждом — по алфавиту."""
    found: list[Migration] = []
    for raw in dirs:
        directory = Path(raw.strip())
        if not raw.strip():
            continue
        if not directory.is_dir():
            log.info(json.dumps({"event": "migrations_dir_missing", "dir": str(directory)}))
            continue
        for path in sorted(directory.glob("*.sql")):
            found.append(Migration(name=f"{directory.name}/{path.name}", path=path))
    return found


async def connect(dsn: str, timeout_s: float) -> asyncpg.Connection:
    deadline = time.monotonic() + timeout_s
    delay = 0.5
    while True:
        try:
            return await asyncpg.connect(dsn)
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            if time.monotonic() >= deadline:
                raise
            log.info(json.dumps({"event": "db_wait", "error": str(exc)}))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)


async def run(dsn: str, dirs: list[str], connect_timeout_s: float = 60.0) -> list[str]:
    """Применяет недостающие миграции, возвращает имена применённых."""
    migrations = discover(dirs)
    conn = await connect(dsn, connect_timeout_s)
    applied_now: list[str] = []
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS public.schema_migrations (
                    name        text PRIMARY KEY,
                    checksum    text,
                    applied_at  timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            # таблица могла быть создана раньше без checksum
            await conn.execute(
                "ALTER TABLE public.schema_migrations ADD COLUMN IF NOT EXISTS checksum text"
            )
            rows = await conn.fetch("SELECT name, checksum FROM public.schema_migrations")
            done = {r["name"]: r["checksum"] for r in rows}

            for m in migrations:
                checksum = m.checksum
                if m.name in done:
                    if done[m.name] and done[m.name] != checksum:
                        log.warning(json.dumps({"event": "migration_changed", "name": m.name}))
                    continue
                sql = m.path.read_text(encoding="utf-8")
                started = time.monotonic()
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO public.schema_migrations (name, checksum) VALUES ($1, $2)",
                        m.name,
                        checksum,
                    )
                applied_now.append(m.name)
                log.info(
                    json.dumps(
                        {
                            "event": "migration_applied",
                            "name": m.name,
                            "duration_ms": round((time.monotonic() - started) * 1000, 1),
                        }
                    )
                )
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
    finally:
        await conn.close()
    return applied_now


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s", stream=sys.stdout)
    dsn = os.getenv("DATABASE_URL", "postgresql://churn:churn@postgres:5432/churn")
    dirs = os.getenv("MIGRATIONS_DIRS", DEFAULT_DIRS).split(",")
    timeout = float(os.getenv("MIGRATE_CONNECT_TIMEOUT_S", "60"))
    try:
        applied = asyncio.run(run(dsn, dirs, timeout))
    except Exception as exc:  # код возврата важен для depends_on: service_completed_successfully
        log.error(json.dumps({"event": "migrate_failed", "error": str(exc)}))
        return 1
    log.info(json.dumps({"event": "migrate_done", "applied": applied}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
