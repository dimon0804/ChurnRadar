from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass

from redis.asyncio import Redis

SESSION_PREFIX = "cr:sess:"
RATE_PREFIX = "cr:rl:login:"
OIDC_STATE_PREFIX = "cr:oidc:"


@dataclass
class Session:
    user_id: str
    username: str
    display_name: str
    roles: list[str]
    auth_method: str
    created_at: float

    def public(self) -> dict:
        return {
            "id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "roles": self.roles,
            "auth_method": self.auth_method,
        }


def _key(token: str) -> str:
    # only a hash of the token is stored, a Redis dump does not leak live sessions
    return SESSION_PREFIX + hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    def __init__(self, redis: Redis, ttl_seconds: int):
        self.redis = redis
        self.ttl = ttl_seconds

    async def create(self, session: Session) -> str:
        token = secrets.token_urlsafe(32)
        await self.redis.set(_key(token), json.dumps(asdict(session)), ex=self.ttl)
        return token

    async def get(self, token: str, *, touch: bool = True) -> Session | None:
        if not token or len(token) > 256:
            return None
        key = _key(token)
        raw = await self.redis.get(key)
        if raw is None:
            return None
        if touch:
            await self.redis.expire(key, self.ttl)
        return Session(**json.loads(raw))

    async def delete(self, token: str) -> None:
        await self.redis.delete(_key(token))


class LoginRateLimiter:
    """Fixed window counter of failed attempts per IP and username."""

    def __init__(self, redis: Redis, max_attempts: int, window_seconds: int):
        self.redis = redis
        self.max_attempts = max_attempts
        self.window = window_seconds

    @staticmethod
    def _key(ip: str, username: str) -> str:
        return f"{RATE_PREFIX}{ip}:{username.lower()[:128]}"

    async def retry_after(self, ip: str, username: str) -> int | None:
        key = self._key(ip, username)
        count = await self.redis.get(key)
        if count is not None and int(count) >= self.max_attempts:
            ttl = await self.redis.ttl(key)
            return max(int(ttl), 1)
        return None

    async def register_failure(self, ip: str, username: str) -> None:
        key = self._key(ip, username)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self.window, nx=True)
            await pipe.execute()

    async def reset(self, ip: str, username: str) -> None:
        await self.redis.delete(self._key(ip, username))


class OidcStateStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 600):
        self.redis = redis
        self.ttl = ttl_seconds

    async def put(self, state: str, data: dict) -> None:
        await self.redis.set(OIDC_STATE_PREFIX + state, json.dumps(data), ex=self.ttl)

    async def pop(self, state: str) -> dict | None:
        raw = await self.redis.getdel(OIDC_STATE_PREFIX + state)
        return json.loads(raw) if raw else None


def now() -> float:
    return time.time()
