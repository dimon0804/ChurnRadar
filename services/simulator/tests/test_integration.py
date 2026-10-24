"""Симулятор против реального Postgres: стартовое состояние потока читается из source.*."""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime

import httpx
import pytest
from conftest import SAMPLE, SCHEMA_FILES, TEST_DATABASE_URL

from app.config import Config
from app.controller import Controller
from app.source_db import make_loader
from app.stream import World

pytestmark = pytest.mark.integration


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
async def seeded_db() -> str:
    asyncpg = pytest.importorskip("asyncpg")
    try:
        conn = await asyncio.wait_for(asyncpg.connect(TEST_DATABASE_URL), 10)
    except (OSError, asyncio.TimeoutError) as exc:
        pytest.skip(f"Postgres недоступен: {exc}")
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    try:
        await conn.execute("DROP SCHEMA IF EXISTS source CASCADE; DROP SCHEMA IF EXISTS scoring CASCADE;")
        for path in SCHEMA_FILES:
            await conn.execute(path.read_text(encoding="utf-8"))
        await conn.executemany(
            "INSERT INTO source.accounts (id, email, balance, withdraw_balance, total_replenished, bonus_balance, "
            "bonus_state) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            [(a["id"], a["email"], a["balance"], a["withdrawBalance"], a["totalReplenished"], a["bonusBalance"],
              a["bonusState"]) for a in data["accounts"]],
        )
        await conn.executemany(
            "INSERT INTO source.services (id, name, owner_id, payment_term, auto_prolong, created_at, expires_at, "
            "status, price) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            [(s["id"], s["name"], s["ownerId"], s["paymentTerm"], s["autoProlong"], _parse(s["createdAt"]),
              _parse(s["expiresAt"]), s["status"], s["price"]) for s in data["services"]],
        )
        await conn.executemany(
            "INSERT INTO source.transactions (id, service_id, amount, status, type, created_at, performed_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            [(t["id"], t["serviceId"], t["amount"], t["status"], t["type"], _parse(t["createdAt"]),
              _parse(t["performedAt"])) for t in data["transactions"]],
        )
    finally:
        await conn.close()
    return TEST_DATABASE_URL


async def test_stream_starts_from_database_state(seeded_db, openapi_validator) -> None:  # type: ignore[no-untyped-def]
    posted: list[dict] = []

    def ingest(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(202, json={"accepted": 1})

    config = Config.from_env()
    world = World(random.Random(3), clock=lambda: datetime(2026, 10, 19, 15, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(ingest)) as client:
        controller = Controller(config, client, state_loader=make_loader(seeded_db), world=world)
        for _ in range(20):
            await controller.step()
    assert len(world.accounts) >= 10 and len(world.services) >= 30
    assert world.next_tx > 100
    validator = openapi_validator("SourceChangeBatch")
    for payload in posted:
        assert not list(validator.iter_errors(payload))
        assert all(t["id"] > 100 for t in payload["transactions"])
    assert controller.events_emitted >= 20
