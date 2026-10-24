"""Чтение текущих данных из source.* — стартовое состояние потока, если снимок не генерировали."""

from __future__ import annotations

import asyncpg

from .stream import World
from .timeutil import iso


def make_loader(dsn: str):  # type: ignore[no-untyped-def]
    async def load(world: World) -> None:
        conn = await asyncpg.connect(dsn, timeout=10)
        try:
            accounts = await conn.fetch(
                "SELECT id, email, balance, withdraw_balance, total_replenished, bonus_balance, bonus_state "
                "FROM source.accounts"
            )
            services = await conn.fetch(
                "SELECT id, name, owner_id, payment_term, auto_prolong, created_at, expires_at, status, price "
                "FROM source.services"
            )
            max_tx = await conn.fetchval("SELECT coalesce(max(id), 0) FROM source.transactions")
        finally:
            await conn.close()
        world.load(
            [
                {
                    "id": r["id"],
                    "email": r["email"],
                    "balance": r["balance"],
                    "withdrawBalance": r["withdraw_balance"],
                    "totalReplenished": r["total_replenished"],
                    "bonusBalance": r["bonus_balance"],
                    "bonusState": r["bonus_state"],
                }
                for r in accounts
            ],
            [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "ownerId": r["owner_id"],
                    "paymentTerm": r["payment_term"],
                    "autoProlong": r["auto_prolong"],
                    "createdAt": iso(r["created_at"]),
                    "expiresAt": iso(r["expires_at"]),
                    "status": r["status"],
                    "price": r["price"],
                }
                for r in services
            ],
            int(max_tx),
        )

    return load
