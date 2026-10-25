"""Интеграция с настоящими Postgres и Redis.

Запускается, только если заданы AUTH_TEST_DATABASE_URL и AUTH_TEST_REDIS_URL (в CI это отдельная
джоба с сервисами postgres и redis). База должна быть пустой и одноразовой: тест прогоняет миграции
вниз и вверх.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import httpx
import pytest
from asgi_lifespan import LifespanManager
from redis.asyncio import Redis

from app.accounts import SsoDenied, sso_sign_in
from app.config import Settings
from app.login_settings import LoginSettings
from app.main import create_app
from app.sessions import Session, now
from app.users import UserRecord

from .conftest import ORIGIN, offline_transport

DB = os.environ.get("AUTH_TEST_DATABASE_URL")
REDIS = os.environ.get("AUTH_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not (DB and REDIS), reason="AUTH_TEST_DATABASE_URL / AUTH_TEST_REDIS_URL not set")
H = {"Origin": ORIGIN}
ROOT = Path(__file__).resolve().parent.parent
# общий журнал действий создаёт мигратор ядра; в тесте auth прогоняем этот файл сами
AUDIT_SQL = ROOT.parent.parent / "db" / "core" / "012_audit_reports.sql"


def alembic(*args: str) -> None:
    subprocess.run(  # noqa: S603 — фиксированная команда, аргументы из теста
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": DB},
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module", autouse=True)
def schema():
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    # миграции обратимы: вниз до 0001 и снова наверх
    alembic("downgrade", "0001")
    alembic("upgrade", "head")
    asyncio.run(apply_core_audit())
    yield


async def apply_core_audit() -> None:
    conn = await asyncpg.connect(DB)
    try:
        # 012 ещё и заводит scoring.report_settings: схема scoring принадлежит ядру
        await conn.execute("CREATE SCHEMA IF NOT EXISTS scoring")
        await conn.execute(AUDIT_SQL.read_text(encoding="utf-8"))
    finally:
        await conn.close()


@contextlib.asynccontextmanager
async def running_app(**overrides):
    options = {"oidc_enabled": False, "saml_enabled": False, **overrides}
    settings = Settings(database_url=DB, redis_url=REDIS, log_level="warning", **options)
    redis = Redis.from_url(REDIS, decode_responses=True)
    await redis.flushdb()
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(offline_transport))
    app = create_app(settings, redis=redis, http=upstream)
    async with LifespanManager(app):
        pool = app.state.ctx.repo.pool
        await pool.execute("DELETE FROM auth.users WHERE username NOT IN ('admin', 'marketer', 'analyst')")
        await pool.execute("UPDATE auth.users SET is_active = true, email = NULL")
        await pool.execute("TRUNCATE audit.events, auth.api_tokens")
        # настройки входа — по умолчанию (всё включено), запись заведётся заново
        await pool.execute("DELETE FROM auth.login_settings")
        app.state.ctx.login_policy.invalidate()
        transport = httpx.ASGITransport(app=app, client=("10.0.0.7", 5000))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://auth", headers={"X-Forwarded-Host": "localhost:8080"}
        ) as c:
            yield c, app.state.ctx
    await upstream.aclose()
    await redis.aclose()


@pytest.fixture
async def app_client():
    async with running_app() as pair:
        yield pair


@pytest.fixture
async def sso_app_client():
    # OIDC настроен в окружении (IdP в тесте не отвечает — для настроек входа это неважно)
    async with running_app(oidc_enabled=True) as pair:
        yield pair


async def login(c, username, password):
    return await c.post("/api/v1/auth/login", json={"username": username, "password": password}, headers=H)


async def test_user_lifecycle_against_postgres_and_redis(app_client):
    c, st = app_client
    assert (await login(c, "admin", "admin")).status_code == 200
    admin_cookie = c.cookies.get("cr_session")

    created = await c.post(
        "/api/v1/auth/users",
        json={"username": "it-user", "display_name": "Интеграция", "email": "it@example.com", "role": "analyst"},
        headers=H,
    )
    assert created.status_code == 201, created.text
    user, temp = created.json()["user"], created.json()["temporary_password"]
    assert (await c.get("/api/v1/auth/users", params={"q": "it@exa"})).json()["total"] == 1

    c.cookies.clear()
    assert (await login(c, "it-user", temp)).status_code == 200
    me = (await c.get("/api/v1/auth/me")).json()
    assert me["email"] == "it@example.com" and me["roles"] == ["analyst"]
    user_cookie = c.cookies.get("cr_session")
    assert await st.redis.scard(f"cr:usess:{user['id']}") == 1

    c.cookies.set("cr_session", admin_cookie)
    blocked = await c.patch(f"/api/v1/auth/users/{user['id']}", json={"status": "blocked"}, headers=H)
    assert blocked.json()["user"]["status"] == "blocked"
    c.cookies.set("cr_session", user_cookie)
    assert (await c.get("/api/v1/auth/me")).status_code == 401
    c.cookies.clear()
    assert (await login(c, "it-user", temp)).json()["error"]["code"] == "account_blocked"

    c.cookies.set("cr_session", admin_cookie)
    assert (await c.delete(f"/api/v1/auth/users/{user['id']}", headers=H)).status_code == 204
    actions = [e["action"] for e in (await c.get("/api/v1/auth/audit")).json()["items"]]
    assert actions[:3] == ["user.delete", "user.block", "user.create"]

    rows = await st.repo.pool.fetch(
        "SELECT action, success, ip, actor_type FROM audit.events WHERE actor = 'it-user' AND action LIKE 'auth.%'"
    )
    assert {r["action"] for r in rows} >= {"auth.login", "auth.login_failed"}
    assert all(r["ip"] == "10.0.0.7" for r in rows)
    admin_rows = await st.repo.pool.fetch(
        "SELECT request_id, ip, object_type FROM audit.events WHERE action = 'user.create' AND actor = 'admin'"
    )
    assert admin_rows and admin_rows[0]["ip"] == "10.0.0.7" and admin_rows[0]["object_type"] == "user"
    assert admin_rows[0]["request_id"]


async def test_api_token_against_postgres_and_redis(app_client):
    c, st = app_client
    await login(c, "admin", "admin")
    created = await c.post(
        "/api/v1/auth/tokens",
        json={"name": "aeza-it", "role": "marketer", "expires_in_days": 7, "allowed_ips": ["10.0.0.0/8"]},
        headers=H,
    )
    assert created.status_code == 201, created.text
    secret, token_id = created.json()["secret"], created.json()["token"]["id"]
    stored = await st.repo.pool.fetchrow("SELECT token_hash, prefix FROM auth.api_tokens WHERE id = $1::uuid", token_id)
    assert secret not in stored["token_hash"] and stored["prefix"] == secret[:7] + "…"

    def verify(ip: str):
        c.cookies.clear()
        return c.get(
            "/internal/verify",
            headers={"Authorization": f"Bearer {secret}", "X-Forwarded-Uri": "/api/v1/customers", "X-Real-Ip": ip},
        )

    ok = await verify("10.1.1.1")
    assert ok.status_code == 200 and ok.headers["X-User-Roles"] == "marketer"
    assert (await verify("192.0.2.1")).status_code == 403
    used = await st.repo.pool.fetchrow(
        "SELECT last_used_at, last_used_ip FROM auth.api_tokens WHERE id = $1::uuid", token_id
    )
    assert used["last_used_at"] is not None and used["last_used_ip"] == "10.1.1.1"

    await login(c, "admin", "admin")
    assert (await c.delete(f"/api/v1/auth/tokens/{token_id}", headers=H)).status_code == 204
    assert (await c.delete("/api/v1/auth/tokens/not-a-uuid", headers=H)).status_code == 404
    assert (await verify("10.1.1.1")).status_code == 401
    rows = await st.repo.pool.fetch(
        "SELECT action, object_type, object_id FROM audit.events WHERE object_type = 'token' ORDER BY id"
    )
    assert [(r["action"], r["object_id"]) for r in rows] == [("token.create", "aeza-it"), ("token.revoke", "aeza-it")]


async def test_sso_jit_and_block_in_postgres(app_client):
    _, st = app_client
    user = await sso_sign_in(st.repo, "saml", "it-subject", "it-sso", "SSO", "sso@example.com", ["analyst"])
    assert user.provider == "saml" and user.email == "sso@example.com"
    again = await sso_sign_in(st.repo, "saml", "it-subject", "it-sso", "SSO", None, ["admin"])
    assert again.id == user.id and again.roles == ["admin"]
    await st.repo.update_user(user.id, is_active=False)
    with pytest.raises(SsoDenied):
        await sso_sign_in(st.repo, "saml", "it-subject", "it-sso", "SSO", None, ["admin"])


async def test_pending_sso_user_in_postgres(app_client):
    c, st = app_client
    await login(c, "admin", "admin")
    resp = await c.post("/api/v1/auth/users", json={"username": "it-pending", "sso_only": True}, headers=H)
    assert resp.json()["user"]["source"] == "sso"
    claimed = await sso_sign_in(st.repo, "oidc", "it-sub-2", "it-pending", "Ждал", None, [])
    assert claimed.provider == "oidc" and claimed.subject == "it-sub-2" and claimed.roles == ["analyst"]


async def test_three_roles_in_postgres(app_client):
    c, st = app_client
    # сид маркетолога и роль marketer проходят ограничение users_roles_check (миграция 0004)
    resp = await login(c, "marketer", "marketer")
    assert resp.status_code == 200 and resp.json()["roles"] == ["marketer"]
    c.cookies.clear()
    await login(c, "admin", "admin")
    analyst = next(u for u in (await c.get("/api/v1/auth/users")).json()["items"] if u["username"] == "analyst")
    promoted = await c.patch(f"/api/v1/auth/users/{analyst['id']}", json={"role": "marketer"}, headers=H)
    assert promoted.json()["user"]["role"] == "marketer"
    back = await c.patch(f"/api/v1/auth/users/{analyst['id']}", json={"role": "analyst"}, headers=H)
    assert back.json()["user"]["role"] == "analyst"
    with pytest.raises(Exception):  # noqa: B017 — любая ошибка ограничения базы
        await st.repo.update_user(analyst["id"], roles=["boss"])


async def sso_admin_cookie(st) -> str:
    """Администратор, который вошёл через OIDC: учётка в базе и живая сессия в Redis."""
    user = await st.repo.create_user(
        UserRecord("", "it-sso-admin", "SSO-админ", ["admin"], "oidc", subject="it-sso-admin-sub")
    )
    return await st.sessions.create(Session(user.id, user.username, user.display_name, ["admin"], "oidc", now()))


async def test_login_settings_against_postgres_and_redis(sso_app_client):
    c, st = sso_app_client
    pool = st.repo.pool
    await pool.execute("DELETE FROM auth.users WHERE username = 'it-sso-admin'")

    # админ по паролю: выключить пароль не даём, отказ записан в журнал
    assert (await login(c, "admin", "admin")).status_code == 200
    denied = await c.patch("/api/v1/auth/login-settings", json={"password_enabled": False}, headers=H)
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "sso_required"
    off_all = await c.patch(
        "/api/v1/auth/login-settings", json={"password_enabled": False, "oidc_enabled": False}, headers=H
    )
    assert off_all.json()["error"]["code"] == "no_login_methods"
    failed = await pool.fetch(
        "SELECT details->>'error' AS code FROM audit.events "
        "WHERE action = 'auth.settings.update' AND NOT success ORDER BY id"
    )
    assert [r["code"] for r in failed] == ["sso_required", "no_login_methods"]

    # аналитик сидит в панели по паролю
    c.cookies.clear()
    assert (await login(c, "analyst", "analyst")).status_code == 200
    analyst_cookie = c.cookies.get("cr_session")

    # SSO-админ выключает пароль: настройка в базе, парольные сессии сброшены
    c.cookies.clear()
    c.cookies.set("cr_session", await sso_admin_cookie(st))
    resp = await c.patch(
        "/api/v1/auth/login-settings",
        json={"password_enabled": False, "sso_default_role": "analyst", "allowed_email_domains": ["Example.com"]},
        headers=H,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked_sessions"] >= 1
    row = await pool.fetchrow("SELECT * FROM auth.login_settings")
    assert row["password_enabled"] is False and row["updated_by"] == "it-sso-admin"
    assert row["sso_default_role"] == "analyst" and list(row["allowed_email_domains"]) == ["example.com"]
    entry = await pool.fetchrow(
        "SELECT actor, object_type, object_id, details::text AS details FROM audit.events "
        "WHERE action = 'auth.settings.update' AND success"
    )
    assert entry["actor"] == "it-sso-admin" and (entry["object_type"], entry["object_id"]) == ("settings", "login")
    assert json.loads(entry["details"])["changes"]["password_enabled"] == {"from": True, "to": False}

    c.cookies.set("cr_session", analyst_cookie)
    assert (await c.get("/api/v1/auth/me")).status_code == 401
    c.cookies.clear()
    closed = await login(c, "admin", "admin")
    assert closed.status_code == 403
    assert closed.json()["error"]["message"] == "Вход по паролю отключён администратором, войдите через SSO"
    assert (await c.get("/api/v1/auth/providers")).json()["password"] == {"enabled": False}

    # аварийный ключ: админа пускаем, аналитика нет
    st.settings.auth_break_glass_password_login = True
    try:
        assert (await login(c, "analyst", "analyst")).status_code == 403
        assert (await login(c, "admin", "admin")).status_code == 200
        assert await pool.fetchval("SELECT count(*) FROM audit.events WHERE action = 'auth.break_glass_login'") == 1
    finally:
        st.settings.auth_break_glass_password_login = False


async def test_jit_policy_against_postgres(sso_app_client):
    _, st = sso_app_client
    pool = st.repo.pool
    await pool.execute("DELETE FROM auth.users WHERE username LIKE 'it-jit-%'")
    closed = LoginSettings(sso_auto_provision=False)
    with pytest.raises(SsoDenied) as exc:
        await sso_sign_in(st.repo, "oidc", "jit-1", "it-jit-new", "Новый", None, ["analyst"], closed)
    assert exc.value.code == "not_provisioned"
    assert await pool.fetchval("SELECT count(*) FROM auth.users WHERE username = 'it-jit-new'") == 0

    picky = LoginSettings(allowed_email_domains=["corp.example"], sso_default_role="marketer")
    with pytest.raises(SsoDenied) as exc:
        await sso_sign_in(st.repo, "oidc", "jit-2", "it-jit-out", "Чужой", "x@gmail.com", [], picky)
    assert exc.value.code == "domain_not_allowed"
    user = await sso_sign_in(st.repo, "oidc", "jit-3", "it-jit-in", "Свой", "me@corp.example", [], picky)
    assert user.roles == ["marketer"] and user.provider == "oidc"
