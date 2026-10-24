"""API-токены интеграций: выпуск, ForwardAuth по Bearer, роль, отзыв, срок, IP, лимит, журнал."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import fakeredis
import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.main import create_app
from app.tokens import CACHE_PREFIX, MemoryTokenRepo, hash_token, ip_allowed, parse_networks
from app.users import MemoryUserRepo

from .conftest import ORIGIN, login, offline_transport

H = {"Origin": ORIGIN}


@pytest.fixture
async def env(settings):
    repo, tokens = MemoryUserRepo(), MemoryTokenRepo()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(offline_transport))
    app = create_app(settings, repo=repo, redis=redis, http=upstream, tokens_repo=tokens)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, client=("10.0.0.5", 5000))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://auth", headers={"X-Forwarded-Host": "localhost:8080"}
        ) as c:
            yield c, repo, tokens, redis
    await upstream.aclose()


async def admin(c):
    assert (await login(c)).status_code == 200


async def issue(c, **body) -> dict:
    r = await c.post("/api/v1/auth/tokens", json={"name": "aeza-crm", **body}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def verify(c, secret: str, method="GET", uri="/api/v1/customers", ip: str | None = None):
    headers = {"Authorization": f"Bearer {secret}", "X-Forwarded-Method": method, "X-Forwarded-Uri": uri}
    if ip:
        headers["X-Real-Ip"] = ip
    # у машинного клиента нет cookie сессии
    c.cookies.clear()
    return await c.get("/internal/verify", headers=headers)


async def test_issue_shows_secret_once_and_stores_only_hash(env):
    c, repo, tokens, _ = env
    await admin(c)
    created = await issue(c, role="analyst", expires_in_days=30)
    secret = created["secret"]
    assert secret.startswith("cr_") and len(secret) > 40
    assert created["token"]["status"] == "active" and created["token"]["prefix"] == secret[:7] + "…"
    (stored,) = tokens.items.values()
    assert stored.token_hash == hash_token(secret) and secret not in repr(stored)
    listed = (await c.get("/api/v1/auth/tokens")).json()["items"]
    assert [t["name"] for t in listed] == ["aeza-crm"] and "secret" not in listed[0]
    # то же имя второй раз — нельзя, пока первый не отозван
    r = await c.post("/api/v1/auth/tokens", json={"name": "AEZA-crm"}, headers=H)
    assert r.status_code == 409


async def test_token_passes_forward_auth_with_its_name_and_role(env):
    c, *_ = env
    await admin(c)
    secret = (await issue(c, role="analyst"))["secret"]
    r = await verify(c, secret)
    assert r.status_code == 200
    assert r.headers["X-User-Name"] == "token:aeza-crm"
    assert r.headers["X-User-Roles"] == "analyst"
    assert r.headers["X-Auth-Method"] == "token"
    assert r.headers["X-User-Id"].startswith("token:")
    # аналитик только читает: отметки по клиентам — от маркетолога
    assert (await verify(c, secret, "PUT", "/api/v1/customers/1/action")).status_code == 403
    await admin(c)
    marketer = (await issue(c, name="aeza-mkt", role="marketer"))["secret"]
    # изменяющий запрос без Origin: CSRF для токена не нужен
    assert (await verify(c, marketer, "PUT", "/api/v1/customers/1/action")).status_code == 200
    # журнал и отчёты меняет только admin
    assert (await verify(c, marketer, "PUT", "/api/v1/reports/schedule")).status_code == 403


async def test_role_of_token_limits_admin_actions(env):
    c, *_ = env
    await admin(c)
    analyst = (await issue(c, name="aeza-ro", role="analyst"))["secret"]
    await admin(c)
    marketer = (await issue(c, name="aeza-mkt", role="marketer"))["secret"]
    boss = (await issue(c, name="aeza-admin", role="admin"))["secret"]
    for secret in (analyst, marketer):
        r = await verify(c, secret, "PATCH", "/api/v1/model/settings")
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert (await verify(c, marketer, "GET", "/api/v1/model/settings")).status_code == 200
    assert (await verify(c, boss, "PATCH", "/api/v1/model/settings")).status_code == 200


async def test_revoked_token_is_rejected_at_once(env):
    c, repo, *_ = env
    await admin(c)
    created = await issue(c)
    assert (await verify(c, created["secret"])).status_code == 200  # запись попала в кеш
    assert (await login(c)).status_code == 200
    r = await c.delete(f"/api/v1/auth/tokens/{created['token']['id']}", headers=H)
    assert r.status_code == 204
    r = await verify(c, created["secret"])
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"
    assert "Bearer" in r.headers["WWW-Authenticate"]
    assert (await verify(c, "cr_" + "x" * 43)).status_code == 401
    actions = [(e.action, e.target) for e in repo.audit]
    assert ("token.create", "aeza-crm") in actions and ("token.revoke", "aeza-crm") in actions
    # отозванный остаётся в списке для истории, имя снова свободно
    await login(c)
    items = (await c.get("/api/v1/auth/tokens")).json()["items"]
    assert items[0]["status"] == "revoked" and items[0]["revoked_by"] == "admin"
    await issue(c)


async def test_expired_token_is_rejected(env):
    c, _, tokens, redis = env
    await admin(c)
    created = await issue(c, expires_in_days=1)
    record = next(iter(tokens.items.values()))
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await redis.delete(CACHE_PREFIX + record.token_hash)
    r = await verify(c, created["secret"])
    assert r.status_code == 401 and r.json()["error"]["code"] == "token_expired"


async def test_ip_allowlist(env):
    c, _, tokens, _ = env
    await admin(c)
    secret = (await issue(c, allowed_ips=["203.0.113.0/24", "2001:db8::1"]))["secret"]
    assert (await verify(c, secret, ip="203.0.113.77")).status_code == 200
    r = await verify(c, secret, ip="198.51.100.1")
    assert r.status_code == 403 and r.json()["error"]["code"] == "ip_not_allowed"
    record = next(iter(tokens.items.values()))
    assert record.last_used_ip == "203.0.113.77" and record.last_used_at is not None


async def test_rate_limit_per_token(env):
    c, *_ = env
    await admin(c)
    secret = (await issue(c, rate_limit_per_min=2))["secret"]
    assert (await verify(c, secret)).status_code == 200
    assert (await verify(c, secret)).status_code == 200
    r = await verify(c, secret)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


async def test_only_admin_session_manages_tokens(env):
    c, *_ = env
    await admin(c)
    secret = (await issue(c, role="admin"))["secret"]
    # токен не может выпускать токены: управление — только из панели, сессией
    c.cookies.clear()
    r = await c.get("/api/v1/auth/tokens", headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 401
    assert (await login(c, "analyst", "analyst")).status_code == 200
    assert (await c.get("/api/v1/auth/tokens")).status_code == 403
    assert (await c.post("/api/v1/auth/tokens", json={"name": "x"}, headers=H)).status_code == 403


async def test_validation_messages(env):
    c, *_ = env
    await admin(c)
    r = await c.post("/api/v1/auth/tokens", json={"name": "aeza", "allowed_ips": ["10.0.0.300"]}, headers=H)
    assert r.status_code == 422 and "10.0.0.300" in r.json()["error"]["message"]
    r = await c.post("/api/v1/auth/tokens", json={"name": "кириллица"}, headers=H)
    assert r.status_code == 422
    r = await c.post("/api/v1/auth/tokens", json={"name": "aeza", "role": "root"}, headers=H)
    assert r.status_code == 422
    # без Origin с cookie — чужой сайт
    r = await c.post("/api/v1/auth/tokens", json={"name": "aeza"})
    assert r.status_code == 403


async def test_logout_and_logins_go_to_audit(env):
    c, repo, *_ = env
    await admin(c)
    assert (await c.post("/api/v1/auth/logout", headers=H)).status_code == 204
    assert [e.action for e in repo.audit] == ["auth.logout"]
    assert (await login(c, "admin", "wrong")).status_code == 401
    assert repo.logins[-1][2] is False


def test_networks_helpers():
    assert parse_networks([" 10.0.0.1 ", "", "192.168.0.0/16"]) == ["10.0.0.1/32", "192.168.0.0/16"]
    with pytest.raises(ValueError):
        parse_networks(["not-an-ip"])
    assert ip_allowed("10.1.2.3", []) is True
    assert ip_allowed(None, ["10.0.0.0/8"]) is False
    assert ip_allowed("::ffff:10.1.2.3", ["10.0.0.0/8"]) is True
    assert ip_allowed("garbage", ["10.0.0.0/8"]) is False
