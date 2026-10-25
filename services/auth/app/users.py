from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

import asyncpg

from . import audit

# порядок — от старшей роли к младшей
VALID_ROLES = ("admin", "marketer", "analyst")
SSO_PROVIDERS = ("oidc", "saml")
# 'sso' — заведён админом «только для SSO», ещё ни разу не входил
PROVIDERS = ("password", "oidc", "saml", "sso")
UPDATABLE = {
    "username",
    "display_name",
    "email",
    "roles",
    "is_active",
    "password_hash",
    "provider",
    "subject",
    "role_override",
}


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
    email: str | None = None
    role_override: bool = False
    created_at: datetime | None = None
    last_login_at: datetime | None = None

    @property
    def role(self) -> str:
        return next((r for r in VALID_ROLES if r in self.roles), "analyst")

    def public(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "email": self.email,
            "role": self.role,
            "status": "active" if self.is_active else "blocked",
            "source": self.provider,
            "has_password": bool(self.password_hash),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }


@dataclass
class AuditEntry:
    id: int
    actor: str
    action: str
    target: str | None
    details: dict
    created_at: datetime

    def public(self) -> dict:
        return {
            "id": self.id,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "details": self.details,
            "created_at": self.created_at.isoformat(),
        }


class UserRepo(Protocol):
    async def get_password_user(self, username: str) -> UserRecord | None: ...

    async def get_user(self, user_id: str) -> UserRecord | None: ...

    async def ensure_password_user(
        self, username: str, password_hash: str, display_name: str, roles: list[str]
    ) -> bool: ...

    async def find_sso_user(self, provider: str, subject: str) -> UserRecord | None: ...

    async def find_pending_sso_user(self, username: str, email: str | None) -> UserRecord | None: ...

    async def username_taken(self, username: str) -> bool: ...

    async def create_user(self, user: UserRecord) -> UserRecord: ...

    async def update_user(self, user_id: str, **changes: Any) -> UserRecord | None: ...

    async def delete_user(self, user_id: str) -> bool: ...

    async def list_users(self, query: str = "") -> list[UserRecord]: ...

    async def count_active_admins(self, providers: tuple[str, ...] | None = None) -> int: ...

    async def touch_login(self, user_id: str) -> None: ...

    async def record_login(self, username: str, provider: str, success: bool, ip: str | None) -> None: ...

    async def add_audit(
        self, actor: str, action: str, target: str | None, details: dict, **context: Any
    ) -> None: ...

    async def list_audit(self, limit: int = 100) -> list[AuditEntry]: ...

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
        email=row["email"],
        role_override=row["role_override"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class PostgresUserRepo:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_password_user(self, username: str) -> UserRecord | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM auth.users WHERE provider = 'password' AND lower(username) = lower($1)",
            username,
        )
        return _row_to_user(row) if row else None

    async def get_user(self, user_id: str) -> UserRecord | None:
        try:
            row = await self.pool.fetchrow("SELECT * FROM auth.users WHERE id = $1::uuid", user_id)
        except (asyncpg.DataError, ValueError):
            return None
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

    async def find_sso_user(self, provider: str, subject: str) -> UserRecord | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM auth.users WHERE provider = $1 AND subject = $2", provider, subject
        )
        return _row_to_user(row) if row else None

    async def find_pending_sso_user(self, username: str, email: str | None) -> UserRecord | None:
        row = await self.pool.fetchrow(
            """
            SELECT * FROM auth.users
            WHERE provider = 'sso'
              AND (lower(username) = lower($1) OR ($2::text IS NOT NULL AND lower(email) = lower($2)))
            ORDER BY lower(username) = lower($1) DESC
            LIMIT 1
            """,
            username,
            email,
        )
        return _row_to_user(row) if row else None

    async def username_taken(self, username: str) -> bool:
        return bool(
            await self.pool.fetchval(
                "SELECT 1 FROM auth.users WHERE lower(username) = lower($1) AND provider IN ('password', 'sso')",
                username,
            )
        )

    async def create_user(self, user: UserRecord) -> UserRecord:
        row = await self.pool.fetchrow(
            """
            INSERT INTO auth.users (username, display_name, email, password_hash, roles, provider, subject,
                                    is_active, role_override)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING *
            """,
            user.username,
            user.display_name,
            user.email,
            user.password_hash,
            user.roles,
            user.provider,
            user.subject,
            user.is_active,
            user.role_override,
        )
        return _row_to_user(row)

    async def update_user(self, user_id: str, **changes: Any) -> UserRecord | None:
        unknown = set(changes) - UPDATABLE
        if unknown:
            raise ValueError(f"unknown fields {unknown}")
        if not changes:
            return await self.get_user(user_id)
        cols = list(changes)
        # имена колонок только из белого списка UPDATABLE, значения — параметрами
        assignments = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(cols))
        row = await self.pool.fetchrow(
            f"UPDATE auth.users SET {assignments}, updated_at = now() WHERE id = $1::uuid RETURNING *",  # noqa: S608
            user_id,
            *[changes[c] for c in cols],
        )
        return _row_to_user(row) if row else None

    async def delete_user(self, user_id: str) -> bool:
        result = await self.pool.execute("DELETE FROM auth.users WHERE id = $1::uuid", user_id)
        return result.endswith("1")

    async def list_users(self, query: str = "") -> list[UserRecord]:
        pattern = f"%{_escape_like(query.strip())}%" if query.strip() else None
        rows = await self.pool.fetch(
            """
            SELECT * FROM auth.users
            WHERE $1::text IS NULL
               OR username ILIKE $1 OR display_name ILIKE $1 OR coalesce(email, '') ILIKE $1
            ORDER BY lower(username), provider
            LIMIT 500
            """,
            pattern,
        )
        return [_row_to_user(r) for r in rows]

    async def count_active_admins(self, providers: tuple[str, ...] | None = None) -> int:
        """Активные администраторы; providers — только привязанные к этим способам входа."""
        return int(
            await self.pool.fetchval(
                "SELECT count(*) FROM auth.users WHERE is_active AND 'admin' = ANY(roles) "
                "AND ($1::text[] IS NULL OR provider = ANY($1::text[]))",
                list(providers) if providers is not None else None,
            )
        )

    async def touch_login(self, user_id: str) -> None:
        await self.pool.execute("UPDATE auth.users SET last_login_at = now() WHERE id = $1::uuid", user_id)

    async def record_login(self, username: str, provider: str, success: bool, ip: str | None) -> None:
        # входы и неудачные попытки — в общий журнал действий (audit.events)
        await audit.write(
            self.pool,
            actor=username[:128],
            actor_type="user" if success else "anonymous",
            action="auth.login" if success else "auth.login_failed",
            object_type="user",
            object_id=username[:128],
            details={"method": provider},
            success=success,
            ip=ip,
        )
        if success and provider == "password":
            await self.pool.execute(
                "UPDATE auth.users SET last_login_at = now() WHERE provider = $1 AND lower(username) = lower($2)",
                provider,
                username,
            )

    async def add_audit(self, actor: str, action: str, target: str | None, details: dict, **context: Any) -> None:
        context.setdefault("object_type", "user")
        await audit.write(self.pool, actor=actor, action=action, object_id=target, details=details, **context)

    async def list_audit(self, limit: int = 100) -> list[AuditEntry]:
        rows = await self.pool.fetch(audit.SELECT_USER_ACTIONS, max(1, min(limit, 500)))
        return [
            AuditEntry(r["id"], r["actor"], r["action"], r["object_id"], json.loads(r["details"]), r["at"])
            for r in rows
        ]

    async def ping(self) -> bool:
        return await self.pool.fetchval("SELECT 1") == 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MemoryUserRepo:
    """In-process repository used by tests and by `AUTH_STORAGE=memory` local runs."""

    users: dict[str, UserRecord] = field(default_factory=dict)
    logins: list[tuple[str, str, bool, str | None, datetime]] = field(default_factory=list)
    audit: list[AuditEntry] = field(default_factory=list)

    async def get_password_user(self, username: str) -> UserRecord | None:
        for user in self.users.values():
            if user.provider == "password" and user.username.lower() == username.lower():
                return user
        return None

    async def get_user(self, user_id: str) -> UserRecord | None:
        return self.users.get(user_id)

    async def ensure_password_user(
        self, username: str, password_hash: str, display_name: str, roles: list[str]
    ) -> bool:
        if await self.get_password_user(username):
            return False
        await self.create_user(UserRecord("", username, display_name, roles, "password", password_hash))
        return True

    async def find_sso_user(self, provider: str, subject: str) -> UserRecord | None:
        for user in self.users.values():
            if user.provider == provider and user.subject == subject:
                return user
        return None

    async def find_pending_sso_user(self, username: str, email: str | None) -> UserRecord | None:
        pending = [u for u in self.users.values() if u.provider == "sso"]
        for user in pending:
            if user.username.lower() == username.lower():
                return user
        for user in pending:
            if email and user.email and user.email.lower() == email.lower():
                return user
        return None

    async def username_taken(self, username: str) -> bool:
        return any(
            u.username.lower() == username.lower() and u.provider in ("password", "sso") for u in self.users.values()
        )

    async def create_user(self, user: UserRecord) -> UserRecord:
        if user.provider not in PROVIDERS:
            raise ValueError(f"unknown provider {user.provider!r}")
        stored = replace(user, id=str(uuid4()), created_at=_now(), roles=list(user.roles))
        self.users[stored.id] = stored
        return stored

    async def update_user(self, user_id: str, **changes: Any) -> UserRecord | None:
        unknown = set(changes) - UPDATABLE
        if unknown:
            raise ValueError(f"unknown fields {unknown}")
        user = self.users.get(user_id)
        if user is None:
            return None
        names = {f.name for f in fields(UserRecord)}
        for key, value in changes.items():
            if key in names:
                setattr(user, key, value)
        return user

    async def delete_user(self, user_id: str) -> bool:
        return self.users.pop(user_id, None) is not None

    async def list_users(self, query: str = "") -> list[UserRecord]:
        q = query.strip().lower()
        found = [
            u
            for u in self.users.values()
            if not q or q in u.username.lower() or q in u.display_name.lower() or q in (u.email or "").lower()
        ]
        return sorted(found, key=lambda u: (u.username.lower(), u.provider))

    async def count_active_admins(self, providers: tuple[str, ...] | None = None) -> int:
        return sum(
            1
            for u in self.users.values()
            if u.is_active and "admin" in u.roles and (providers is None or u.provider in providers)
        )

    async def touch_login(self, user_id: str) -> None:
        if user_id in self.users:
            self.users[user_id].last_login_at = _now()

    async def record_login(self, username: str, provider: str, success: bool, ip: str | None) -> None:
        self.logins.append((username, provider, success, ip, _now()))
        if success and provider == "password":
            user = await self.get_password_user(username)
            if user:
                user.last_login_at = _now()

    async def add_audit(self, actor: str, action: str, target: str | None, details: dict, **context: Any) -> None:
        self.audit.append(AuditEntry(len(self.audit) + 1, actor, action, target, dict(details), _now()))

    async def list_audit(self, limit: int = 100) -> list[AuditEntry]:
        return list(reversed(self.audit))[:limit]

    async def ping(self) -> bool:
        return True
