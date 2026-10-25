from __future__ import annotations

from .conftest import ORIGIN, login


async def test_login_sets_http_only_cookie(client):
    resp = await login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "admin"
    assert body["roles"] == ["admin"]
    assert body["auth_method"] == "password"
    cookie = resp.headers["set-cookie"]
    assert "cr_session=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()


async def test_wrong_password_is_401_with_error_format(client):
    resp = await login(client, password="nope")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"
    assert "set-cookie" not in resp.headers


async def test_unknown_user_is_401(client):
    resp = await login(client, username="ghost")
    assert resp.status_code == 401


async def test_empty_body_is_422(client):
    resp = await client.post("/api/v1/auth/login", json={"username": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_foreign_origin_login_rejected(client):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403


async def test_me_without_session_is_401(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_me_with_cookie_and_bearer(client):
    resp = await login(client)
    token = resp.cookies["cr_session"]
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["display_name"] == "Администратор"

    client.cookies.clear()
    bearer = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert bearer.status_code == 200


async def test_logout_kills_session(client):
    await login(client)
    token = client.cookies["cr_session"]
    resp = await client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
    assert resp.status_code == 204
    client.cookies.clear()
    again = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert again.status_code == 401


async def test_logout_requires_same_origin_for_cookie(client):
    await login(client)
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 403


async def test_rate_limit_after_five_failures(client):
    for _ in range(5):
        assert (await login(client, password="bad")).status_code == 401
    blocked = await login(client, password="admin")
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    assert blocked.json()["error"]["code"] == "too_many_attempts"


async def test_rate_limit_is_per_username(client):
    for _ in range(5):
        await login(client, password="bad")
    assert (await login(client, username="analyst", password="analyst")).status_code == 200


async def test_success_resets_failures(client):
    for _ in range(4):
        await login(client, password="bad")
    assert (await login(client)).status_code == 200
    for _ in range(4):
        await login(client, password="bad")
    assert (await login(client)).status_code == 200


async def test_session_ttl_slides(client, redis):
    await login(client)
    key = next(k for k in await redis.keys("cr:sess:*"))
    await redis.expire(key, 10)
    await client.get("/api/v1/auth/me")
    assert await redis.ttl(key) > 3600


async def test_providers_without_idp(client):
    resp = await client.get("/api/v1/auth/providers")
    assert resp.json() == {"password": {"enabled": True}, "oidc": {"enabled": False}, "saml": {"enabled": False}}


async def test_oidc_login_404_when_disabled(client):
    resp = await client.get("/api/v1/auth/oidc/login")
    assert resp.status_code == 404


async def test_saml_endpoints_404_when_disabled(client):
    assert (await client.get("/api/v1/auth/saml/login")).status_code == 404
    assert (await client.get("/api/v1/auth/saml/metadata")).status_code == 404


async def test_login_audit_recorded(client, repo):
    await login(client, password="bad")
    await login(client)
    assert [(u, ok) for u, _, ok, _, _ in repo.logins] == [("admin", False), ("admin", True)]
