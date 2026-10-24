from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]
sys.path.insert(0, str(SERVICE_DIR))
try:
    import riskcore  # noqa: F401
except ImportError:  # локальный запуск без установленного пакета
    sys.path.insert(0, str(REPO_ROOT / "libs" / "riskcore"))

SAMPLE = REPO_ROOT / "data" / "sample" / "data.json"
SCHEMA_FILES = sorted((REPO_ROOT / "db" / "core").glob("*.sql"))

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql://churn:churn@localhost:55433/churn")
TEST_NATS_URL = os.getenv("TEST_NATS_URL", "nats://localhost:54223")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: тесты против реальных Postgres и NATS")


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def load_snapshot(
    conn: Any, data: dict[str, Any], reference_time: datetime | None, *, truncate_history: bool = True
) -> None:
    """Загрузка снимка в source.* напрямую (как это делает ingest), через COPY."""
    async with conn.transaction():
        if truncate_history:
            await conn.execute("TRUNCATE source.status_history")
        await conn.execute("TRUNCATE source.transactions, source.services, source.accounts")
        await conn.copy_records_to_table(
            "accounts",
            schema_name="source",
            columns=["id", "email", "balance", "withdraw_balance", "total_replenished", "bonus_balance", "bonus_state"],
            records=[
                (a["id"], a["email"], a["balance"], a["withdrawBalance"], a["totalReplenished"], a["bonusBalance"],
                 a["bonusState"])
                for a in data["accounts"]
            ],
        )
        await conn.copy_records_to_table(
            "services",
            schema_name="source",
            columns=["id", "name", "owner_id", "payment_term", "auto_prolong", "created_at", "expires_at", "status",
                     "price"],
            records=[
                (s["id"], s["name"], s["ownerId"], s["paymentTerm"], s["autoProlong"], _parse(s["createdAt"]),
                 _parse(s["expiresAt"]), s["status"], s["price"])
                for s in data["services"]
            ],
        )
        await conn.copy_records_to_table(
            "transactions",
            schema_name="source",
            columns=["id", "service_id", "amount", "status", "type", "created_at", "performed_at"],
            records=[
                (t["id"], t["serviceId"], t["amount"], t["status"], t["type"], _parse(t["createdAt"]),
                 _parse(t.get("performedAt")))
                for t in data["transactions"]
            ],
        )
        await conn.execute("UPDATE source.clock SET reference_time = $1, updated_at = now()", reference_time)


@pytest.fixture(scope="session")
def sample_data() -> dict[str, Any]:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


async def _reset_schema(dsn: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS source CASCADE; DROP SCHEMA IF EXISTS scoring CASCADE;")
        for path in SCHEMA_FILES:
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


@pytest.fixture
async def db_pool() -> AsyncIterator[Any]:
    asyncpg = pytest.importorskip("asyncpg")
    try:
        await asyncio.wait_for(_reset_schema(TEST_DATABASE_URL), timeout=10)
    except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres недоступен: {exc}")
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()
