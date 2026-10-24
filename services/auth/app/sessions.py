from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass

from redis.asyncio import Redis

SESSION_PREFIX = "cr:sess:"
USER_SESSIONS_PREFIX = "cr:usess:"
RATE_PREFIX = "cr:rl:login:"
OIDC_STATE_PREFIX = "cr:oidc:"
SAML_REQUEST_PREFIX = "cr:saml:req:"
SAML_SEEN_PREFIX = "cr:saml:seen:"


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
        key = _key(token)
        index = USER_SESSIONS_PREFIX + session.user_id
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.set(key, json.dumps(asdict(session)), ex=self.ttl)
            # список сессий пользователя: при блокировке или смене роли их надо сбросить разом
            pipe.sadd(index, key)
            pipe.expire(index, self.ttl * 2)
            await pipe.execute()
        return token

    async def revoke_user(self, user_id: str, *, keep_token: str | None = None) -> int:
        """Drop every session of the user (optionally except the current one)."""
        index = USER_SESSIONS_PREFIX + user_id
        keys = set(await self.redis.smembers(index))
        if keep_token:
            keys.discard(_key(keep_token))
        if keys:
            await self.redis.delete(*keys)
            await self.redis.srem(index, *keys)
        return len(keys)

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
    """One-shot state of a redirect login: OIDC `state` or the ID of a SAML AuthnRequest."""

    def __init__(self, redis: Redis, ttl_seconds: int = 600, prefix: str = OIDC_STATE_PREFIX):
        self.redis = redis
        self.ttl = ttl_seconds
        self.prefix = prefix

    async def put(self, state: str, data: dict) -> None:
        await self.redis.set(self.prefix + state, json.dumps(data), ex=self.ttl)

    async def pop(self, state: str) -> dict | None:
        if not state or len(state) > 256:
            return None
        raw = await self.redis.getdel(self.prefix + state)
        return json.loads(raw) if raw else None


class ReplayGuard:
    """Remembers SAML assertion IDs until they expire: the same assertion is accepted once."""

    def __init__(self, redis: Redis, prefix: str = SAML_SEEN_PREFIX):
        self.redis = redis
        self.prefix = prefix

    async def first_use(self, assertion_id: str, ttl_seconds: int) -> bool:
        key = self.prefix + hashlib.sha256(assertion_id.encode()).hexdigest()
        return bool(await self.redis.set(key, "1", ex=max(int(ttl_seconds), 60), nx=True))


def now() -> float:
    return time.time()
