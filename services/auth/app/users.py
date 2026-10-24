from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

import asyncpg

VALID_ROLES = ("admin", "analyst")


@dataclass
class UserRecord:
    id: str
    username: str
    display_name: str
    roles: list[str]
    provider: str = "password"
    password_hash: str | None = None
    subject: str | None = None
    is_active: bool = True


class UserRepo(Protocol):
    async def get_password_user(self, username: str) -> UserRecord | None: ...

    async def ensure_password_user(
        self, username: str, password_hash: str, display_name: str, roles: list[str]
    ) -> bool: ...

    async def upsert_oidc_user(
        self, subject: str, username: str, display_name: str, roles: list[str]
    ) -> UserRecord: ...

    async def record_login(self, username: str, provider: str, success: bool, ip: str | None) -> None: ...

    async def ping(self) -> bool: ...


def _row_to_user(row: asyncpg.Record) -> UserRecord:
    return UserRecord(
        id=str(row["id"]),
        username=row["username"],
        display_name=row["display_name"],
        roles=list(row["roles"]),
        provider=row["provider"],
        password_hash=row["password_hash"],
        subject=row["subject"],
        is_active=row["is_active"],
    )


class PostgresUserRepo:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_password_user(self, username: str) -> UserRecord | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM auth.users WHERE provider = 'password' AND lower(username) = lower($1)",
            username,
        )
        return _row_to_user(row) if row else None

    async def ensure_password_user(
        self, username: str, password_hash: str, display_name: str, roles: list[str]
    ) -> bool:
        result = await self.pool.execute(
            """
            INSERT INTO auth.users (username, display_name, password_hash, roles, provider)
            VALUES ($1, $2, $3, $4, 'password')
            ON CONFLICT (lower(username)) WHERE provider = 'password' DO NOTHING
            """,
            username,
            display_name,
            password_hash,
            roles,
        )
        return result.endswith("1")

    async def upsert_oidc_user(self, subject: str, username: str, display_name: str, roles: list[str]) -> UserRecord:
        row = await self.pool.fetchrow(
            """
            INSERT INTO auth.users (username, display_name, roles, provider, subject, last_login_at)
            VALUES ($1, $2, $3, 'oidc', $4, now())
            ON CONFLICT (provider, subject) WHERE subject IS NOT NULL
            DO UPDATE SET username = EXCLUDED.username,
                          display_name = EXCLUDED.display_name,
                          roles = EXCLUDED.roles,
                          last_login_at = now()
            RETURNING *
            """,
            username,
            display_name,
            roles,
            subject,
        )
        return _row_to_user(row)

    async def record_login(self, username: str, provider: str, success: bool, ip: str | None) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO auth.login_events (username, provider, success, ip) VALUES ($1, $2, $3, $4)",
                username[:128],
                provider,
                success,
                ip,
            )
            if success:
                await conn.execute(
                    "UPDATE auth.users SET last_login_at = now() WHERE provider = $1 AND lower(username) = lower($2)",
                    provider,
                    username,
                )

    async def ping(self) -> bool:
        return await self.pool.fetchval("SELECT 1") == 1


@dataclass
class MemoryUserRepo:
    """In-process repository used by tests and by `AUTH_STORAGE=memory` local runs."""

    users: dict[str, UserRecord] = field(default_factory=dict)
    logins: list[tuple[str, str, bool, str | None, datetime]] = field(default_factory=list)

    async def get_password_user(self, username: str) -> UserRecord | None:
        for user in self.users.values():
            if user.provider == "password" and user.username.lower() == username.lower():
                return user
        return None

    async def ensure_password_user(
        self, username: str, password_hash: str, display_name: str, roles: list[str]
    ) -> bool:
        if await self.get_password_user(username):
            return False
        user = UserRecord(str(uuid4()), username, display_name, roles, "password", password_hash)
        self.users[user.id] = user
        return True

    async def upsert_oidc_user(self, subject: str, username: str, display_name: str, roles: list[str]) -> UserRecord:
        for user in self.users.values():
            if user.provider == "oidc" and user.subject == subject:
                user.username, user.display_name, user.roles = username, display_name, roles
                return user
        user = UserRecord(str(uuid4()), username, display_name, roles, "oidc", None, subject)
        self.users[user.id] = user
        return user

    async def record_login(self, username: str, provider: str, success: bool, ip: str | None) -> None:
        self.logins.append((username, provider, success, ip, datetime.now(timezone.utc)))

    async def ping(self) -> bool:
        return True
