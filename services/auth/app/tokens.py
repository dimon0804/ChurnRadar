"""API-токены для интеграций: внешние системы ходят в ChurnRadar с `Authorization: Bearer cr_…`.

Токен создаёт администратор в панели: имя, роль (admin, marketer, analyst), срок действия,
необязательный список разрешённых IP или подсетей. Сам токен показывается один раз, в базе —
только SHA-256. Проверяет токен ForwardAuth (/internal/verify) на каждом запросе через шлюз,
поэтому запись токена кешируется в Redis на несколько секунд; отзыв сбрасывает кеш сразу.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

import asyncpg
from redis.asyncio import Redis

TOKEN_PREFIX = "cr_"  # noqa: S105 — приставка, не секрет
TOKEN_ROLES = ("admin", "marketer", "analyst")
CACHE_PREFIX = "cr:tok:"
USED_PREFIX = "cr:tok:used:"
RATE_PREFIX = "cr:rl:tok:"
MISSING = "-"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def looks_like_token(value: str | None) -> bool:
    return bool(value) and value.startswith(TOKEN_PREFIX) and len(value) <= 256


def parse_networks(values: list[str]) -> list[str]:
    """Проверяет и нормализует список IP/подсетей. ValueError — с понятным текстом."""
    out: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value:
            continue
        try:
            out.append(str(ipaddress.ip_network(value, strict=False)))
        except ValueError as exc:
            raise ValueError(f"«{value}» — не IP-адрес и не подсеть (пример: 203.0.113.10 или 10.0.0.0/8)") from exc
    if len(out) > 50:
        raise ValueError("Не больше 50 адресов и подсетей")
    return out


def ip_allowed(ip: str | None, networks: list[str]) -> bool:
    if not networks:
        return True
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in ipaddress.ip_network(n, strict=False) for n in networks)


@dataclass
class TokenRecord:
    id: str
    name: str
    role: str
    token_hash: str
    prefix: str
    created_by: str
    allowed_ips: list[str] = field(default_factory=list)
    rate_limit_per_min: int | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    last_used_at: datetime | None = None
    last_used_ip: str | None = None

    def status(self, now: datetime | None = None) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= (now or datetime.now(timezone.utc)):
            return "expired"
        return "active"

    @property
    def header_name(self) -> str:
        # в X-User-Name токен не должен совпасть с логином сотрудника (настройки уведомлений
        # и журнал завязаны на имя), поэтому у него своя приставка
        return f"token:{self.name}"

    def public(self) -> dict:
        def iso(v: datetime | None) -> str | None:
            return v.isoformat() if v else None

        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "prefix": self.prefix,
            "allowed_ips": list(self.allowed_ips),
            "rate_limit_per_min": self.rate_limit_per_min,
            "status": self.status(),
            "expires_at": iso(self.expires_at),
            "created_at": iso(self.created_at),
            "created_by": self.created_by,
            "revoked_at": iso(self.revoked_at),
            "revoked_by": self.revoked_by,
            "last_used_at": iso(self.last_used_at),
            "last_used_ip": self.last_used_ip,
        }

    def cache(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "name": self.name,
                "role": self.role,
                "allowed_ips": self.allowed_ips,
                "rate_limit_per_min": self.rate_limit_per_min,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "revoked": self.revoked_at is not None,
            }
        )


@dataclass(frozen=True)
class CachedToken:
    id: str
    name: str
    role: str
    allowed_ips: list[str]
    rate_limit_per_min: int | None
    expires_at: datetime | None
    revoked: bool

    @classmethod
    def load(cls, raw: str) -> CachedToken:
        d = json.loads(raw)
        exp = datetime.fromisoformat(d["expires_at"]) if d["expires_at"] else None
        return cls(d["id"], d["name"], d["role"], d["allowed_ips"], d["rate_limit_per_min"], exp, d["revoked"])

    @property
    def header_name(self) -> str:
        return f"token:{self.name}"


class TokenRepo(Protocol):
    async def create(self, record: TokenRecord) -> TokenRecord: ...

    async def list(self) -> list[TokenRecord]: ...

    async def get(self, token_id: str) -> TokenRecord | None: ...

    async def by_hash(self, token_hash: str) -> TokenRecord | None: ...

    async def name_taken(self, name: str) -> bool: ...

    async def revoke(self, token_id: str, by: str) -> TokenRecord | None: ...

    async def touch(self, token_id: str, ip: str | None) -> None: ...


def _row(row: Any) -> TokenRecord:
    return TokenRecord(
        id=str(row["id"]),
        name=row["name"],
        role=row["role"],
        token_hash=row["token_hash"],
        prefix=row["prefix"],
        created_by=row["created_by"],
        allowed_ips=list(row["allowed_ips"] or []),
        rate_limit_per_min=row["rate_limit_per_min"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
        revoked_by=row["revoked_by"],
        last_used_at=row["last_used_at"],
        last_used_ip=row["last_used_ip"],
    )


class PostgresTokenRepo:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def create(self, record: TokenRecord) -> TokenRecord:
        row = await self.pool.fetchrow(
            """
            INSERT INTO auth.api_tokens (name, role, token_hash, prefix, allowed_ips, rate_limit_per_min,
                                         expires_at, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING *
            """,
            record.name,
            record.role,
            record.token_hash,
            record.prefix,
            record.allowed_ips,
            record.rate_limit_per_min,
            record.expires_at,
            record.created_by,
        )
        return _row(row)

    async def list(self) -> list[TokenRecord]:
        rows = await self.pool.fetch(
            "SELECT * FROM auth.api_tokens ORDER BY revoked_at IS NOT NULL, created_at DESC LIMIT 500"
        )
        return [_row(r) for r in rows]

    async def get(self, token_id: str) -> TokenRecord | None:
        try:
            row = await self.pool.fetchrow("SELECT * FROM auth.api_tokens WHERE id = $1::uuid", token_id)
        except (asyncpg.DataError, ValueError):  # кривой uuid из адресной строки — просто «не найден»
            return None
        return _row(row) if row else None

    async def by_hash(self, token_hash: str) -> TokenRecord | None:
        row = await self.pool.fetchrow("SELECT * FROM auth.api_tokens WHERE token_hash = $1", token_hash)
        return _row(row) if row else None

    async def name_taken(self, name: str) -> bool:
        return bool(
            await self.pool.fetchval(
                "SELECT 1 FROM auth.api_tokens WHERE lower(name) = lower($1) AND revoked_at IS NULL", name
            )
        )

    async def revoke(self, token_id: str, by: str) -> TokenRecord | None:
        row = await self.pool.fetchrow(
            "UPDATE auth.api_tokens SET revoked_at = now(), revoked_by = $2 "
            "WHERE id = $1::uuid AND revoked_at IS NULL RETURNING *",
            token_id,
            by,
        )
        return _row(row) if row else None

    async def touch(self, token_id: str, ip: str | None) -> None:
        await self.pool.execute(
            "UPDATE auth.api_tokens SET last_used_at = now(), last_used_ip = $2 WHERE id = $1::uuid", token_id, ip
        )


@dataclass
class MemoryTokenRepo:
    items: dict[str, TokenRecord] = field(default_factory=dict)

    async def create(self, record: TokenRecord) -> TokenRecord:
        stored = replace(record, id=str(uuid4()), created_at=datetime.now(timezone.utc))
        self.items[stored.id] = stored
        return stored

    async def list(self) -> list[TokenRecord]:
        return sorted(self.items.values(), key=lambda t: (t.revoked_at is not None, -t.created_at.timestamp()))

    async def get(self, token_id: str) -> TokenRecord | None:
        return self.items.get(token_id)

    async def by_hash(self, token_hash: str) -> TokenRecord | None:
        return next((t for t in self.items.values() if t.token_hash == token_hash), None)

    async def name_taken(self, name: str) -> bool:
        return any(t.name.lower() == name.lower() and t.revoked_at is None for t in self.items.values())

    async def revoke(self, token_id: str, by: str) -> TokenRecord | None:
        token = self.items.get(token_id)
        if token is None or token.revoked_at is not None:
            return None
        token.revoked_at, token.revoked_by = datetime.now(timezone.utc), by
        return token

    async def touch(self, token_id: str, ip: str | None) -> None:
        if token_id in self.items:
            self.items[token_id].last_used_at = datetime.now(timezone.utc)
            self.items[token_id].last_used_ip = ip


class TokenDenied(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers


class TokenService:
    def __init__(
        self,
        repo: TokenRepo,
        redis: Redis,
        *,
        rate_limit_per_min: int = 600,
        cache_ttl_s: int = 15,
        touch_every_s: int = 60,
    ) -> None:
        self.repo = repo
        self.redis = redis
        self.rate_limit_per_min = rate_limit_per_min
        self.cache_ttl_s = cache_ttl_s
        self.touch_every_s = touch_every_s

    async def issue(
        self,
        *,
        name: str,
        role: str,
        created_by: str,
        expires_in_days: int | None,
        allowed_ips: list[str],
        rate_limit_per_min: int | None,
    ) -> tuple[TokenRecord, str]:
        secret = new_token()
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days) if expires_in_days else None
        record = await self.repo.create(
            TokenRecord(
                id="",
                name=name,
                role=role,
                token_hash=hash_token(secret),
                prefix=secret[:7] + "…",
                created_by=created_by,
                allowed_ips=allowed_ips,
                rate_limit_per_min=rate_limit_per_min,
                expires_at=expires_at,
            )
        )
        await self.redis.delete(CACHE_PREFIX + record.token_hash)
        return record, secret

    async def revoke(self, token_id: str, by: str) -> TokenRecord | None:
        record = await self.repo.revoke(token_id, by)
        if record is not None:
            await self.redis.delete(CACHE_PREFIX + record.token_hash)
        return record

    async def _lookup(self, token_hash: str) -> CachedToken | None:
        key = CACHE_PREFIX + token_hash
        raw = await self.redis.get(key)
        if raw == MISSING:
            return None
        if raw:
            return CachedToken.load(raw)
        record = await self.repo.by_hash(token_hash)
        await self.redis.set(key, record.cache() if record else MISSING, ex=self.cache_ttl_s)
        return CachedToken.load(record.cache()) if record else None

    async def authenticate(self, token: str, ip: str | None) -> CachedToken:
        """Токен для ForwardAuth. TokenDenied: 401 — нет/отозван/истёк, 403 — чужой IP, 429 — лимит."""
        found = await self._lookup(hash_token(token))
        if found is None or found.revoked:
            raise TokenDenied(401, "invalid_token", "API-токен не найден или отозван", _bearer_challenge())
        if found.expires_at is not None and found.expires_at <= datetime.now(timezone.utc):
            raise TokenDenied(401, "token_expired", "Срок действия API-токена истёк", _bearer_challenge())
        if not ip_allowed(ip, found.allowed_ips):
            raise TokenDenied(403, "ip_not_allowed", f"С адреса {ip or 'неизвестно'} этим токеном пользоваться нельзя")
        retry = await self._rate(found)
        if retry:
            raise TokenDenied(
                429,
                "rate_limited",
                f"Слишком много запросов с этим токеном. Повторите через {retry} с",
                {"Retry-After": str(retry)},
            )
        if await self.redis.set(USED_PREFIX + found.id, "1", ex=self.touch_every_s, nx=True):
            await self.repo.touch(found.id, ip)
        return found

    async def _rate(self, token: CachedToken) -> int:
        limit = token.rate_limit_per_min or self.rate_limit_per_min
        window = int(time.time() // 60)
        key = f"{RATE_PREFIX}{token.id}:{window}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, 120)
            count, _ = await pipe.execute()
        if int(count) > limit:
            return max(1, 60 - int(time.time()) % 60)
        return 0


def _bearer_challenge() -> dict:
    return {"WWW-Authenticate": 'Bearer realm="churnradar", error="invalid_token"'}
