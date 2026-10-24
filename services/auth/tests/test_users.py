from __future__ import annotations

import pytest

from app.accounts import SsoDenied, sso_sign_in
from app.users import MemoryUserRepo, UserRecord

from .conftest import ORIGIN, login

H = {"Origin": ORIGIN}


async def as_admin(client):
    assert (await login(client)).status_code == 200


async def create(client, **body):
    payload = {"username": "petya", "display_name": "Пётр", "role": "analyst", **body}
    return await client.post("/api/v1/auth/users", json=payload, headers=H)


def find(items, username, source=None):
    return next(u for u in items if u["username"] == username and (source is None or u["source"] == source))


async def test_only_admin_manages_users(client):
    assert (await client.get("/api/v1/auth/users")).status_code == 401
    await login(client, "analyst", "analyst")
    resp = await client.get("/api/v1/auth/users")
    assert resp.status_code == 403
    assert (await create(client)).status_code == 403


async def test_list_and_search(client):
    await as_admin(client)
    body = (await client.get("/api/v1/auth/users")).json()
    assert {u["username"] for u in body["items"]} == {"admin", "marketer", "analyst"}
    admin = find(body["items"], "admin")
    assert admin["role"] == "admin" and admin["status"] == "active" and admin["source"] == "password"
    assert admin["last_login_at"]
    assert (await client.get("/api/v1/auth/users", params={"q": "анал"})).json()["total"] == 1


async def test_csrf_required_for_changes(client):
    await as_admin(client)
    resp = await client.post("/api/v1/auth/users", json={"username": "x1"}, headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_rejected"


async def test_create_with_password_and_login(client):
    await as_admin(client)
    resp = await create(client, password="correct-horse", email="petya@example.com")
    assert resp.status_code == 201
    user = resp.json()["user"]
    assert user["email"] == "petya@example.com" and user["role"] == "analyst"
    assert "temporary_password" not in resp.json()
    client.cookies.clear()
    me = await login(client, "petya", "correct-horse")
    assert me.status_code == 200
    assert me.json()["email"] == "petya@example.com"
    assert me.json()["roles"] == ["analyst"]


async def test_create_with_generated_password_shown_once(client):
    await as_admin(client)
    resp = await create(client)
    temp = resp.json()["temporary_password"]
    assert len(temp) >= 12
    client.cookies.clear()
    assert (await login(client, "petya", temp)).status_code == 200


async def test_validation_messages(client):
    await as_admin(client)
    weak = await create(client, password="123")
    assert weak.status_code == 422 and "8" in weak.json()["error"]["message"]
    bad = await create(client, username="плохой логин")
    assert bad.status_code == 422 and "Логин" in bad.json()["error"]["message"]
    mail = await create(client, email="not-an-email")
    assert mail.status_code == 422 and "почты" in mail.json()["error"]["message"]
    dup = await create(client, username="Analyst", password="long-enough-1")
    assert dup.status_code == 409


async def test_block_drops_sessions_and_login(client, redis):
    await as_admin(client)
    user_id = (await create(client, password="correct-horse")).json()["user"]["id"]
    admin_cookie = client.cookies.get("cr_session")

    client.cookies.clear()
    await login(client, "petya", "correct-horse")
    petya_cookie = client.cookies.get("cr_session")

    client.cookies.clear()
    client.cookies.set("cr_session", admin_cookie)
    resp = await client.patch(f"/api/v1/auth/users/{user_id}", json={"status": "blocked"}, headers=H)
    assert resp.json()["user"]["status"] == "blocked"

    client.cookies.clear()
    client.cookies.set("cr_session", petya_cookie)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    client.cookies.clear()
    blocked = await login(client, "petya", "correct-horse")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "account_blocked"
    # чужой пароль по-прежнему даёт обычный отказ, статус учётки не раскрываем
    assert (await login(client, "petya", "wrong-password")).status_code == 401


async def test_role_change_resets_sessions(client):
    await as_admin(client)
    user_id = (await create(client, password="correct-horse")).json()["user"]["id"]
    admin_cookie = client.cookies.get("cr_session")
    client.cookies.clear()
    await login(client, "petya", "correct-horse")
    petya_cookie = client.cookies.get("cr_session")
    client.cookies.set("cr_session", admin_cookie)
    resp = await client.patch(f"/api/v1/auth/users/{user_id}", json={"role": "admin"}, headers=H)
    assert resp.json()["user"]["role"] == "admin"
    client.cookies.set("cr_session", petya_cookie)
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_cannot_block_demote_or_delete_self(client, repo):
    await as_admin(client)
    admin_id = next(u.id for u in repo.users.values() if u.username == "admin")
    for change in ({"status": "blocked"}, {"role": "analyst"}):
        resp = await client.patch(f"/api/v1/auth/users/{admin_id}", json=change, headers=H)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "self_change"
    assert (await client.delete(f"/api/v1/auth/users/{admin_id}", headers=H)).status_code == 400


async def test_last_active_admin_is_protected(client, repo):
    """Actor's own admin rights vanished in the DB meanwhile: the target is then the last active admin."""
    await as_admin(client)
    boss = (await create(client, username="boss", role="admin", password="boss-password")).json()["user"]
    admin_id = next(u.id for u in repo.users.values() if u.username == "admin")
    await repo.update_user(admin_id, roles=["analyst"])
    url = f"/api/v1/auth/users/{boss['id']}"
    for resp in (
        await client.patch(url, json={"status": "blocked"}, headers=H),
        await client.patch(url, json={"role": "analyst"}, headers=H),
        await client.delete(url, headers=H),
    ):
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "last_admin"
    # при двух активных админах одного заблокировать можно
    await repo.update_user(admin_id, roles=["admin"])
    assert (await client.patch(url, json={"status": "blocked"}, headers=H)).status_code == 200


async def test_reset_password_generated(client):
    await as_admin(client)
    user_id = (await create(client, password="correct-horse")).json()["user"]["id"]
    resp = await client.post(f"/api/v1/auth/users/{user_id}/password", json={}, headers=H)
    temp = resp.json()["temporary_password"]
    client.cookies.clear()
    assert (await login(client, "petya", "correct-horse")).status_code == 401
    assert (await login(client, "petya", temp)).status_code == 200


async def test_reset_password_explicit(client):
    await as_admin(client)
    user_id = (await create(client, password="correct-horse")).json()["user"]["id"]
    resp = await client.post(f"/api/v1/auth/users/{user_id}/password", json={"password": "new-password-1"}, headers=H)
    assert resp.status_code == 204
    client.cookies.clear()
    assert (await login(client, "petya", "new-password-1")).status_code == 200


async def test_delete_and_audit(client):
    await as_admin(client)
    user_id = (await create(client, password="correct-horse")).json()["user"]["id"]
    await client.patch(
        f"/api/v1/auth/users/{user_id}", json={"email": "p@example.com", "display_name": "Петя"}, headers=H
    )
    assert (await client.delete(f"/api/v1/auth/users/{user_id}", headers=H)).status_code == 204
    assert (await client.delete(f"/api/v1/auth/users/{user_id}", headers=H)).status_code == 404
    audit = (await client.get("/api/v1/auth/audit")).json()["items"]
    assert [e["action"] for e in audit][:3] == ["user.delete", "user.update", "user.create"]
    assert all(e["actor"] == "admin" and e["target"] == "petya" for e in audit[:3])


async def test_sso_only_user(client, repo):
    await as_admin(client)
    resp = await create(client, username="mariya", sso_only=True, role="admin")
    assert "temporary_password" not in resp.json()
    user = resp.json()["user"]
    assert user["source"] == "sso" and not user["has_password"]
    pw = await client.post(f"/api/v1/auth/users/{user['id']}/password", json={}, headers=H)
    assert pw.status_code == 400


async def test_profile_email_and_password(client):
    await login(client, "analyst", "analyst")
    resp = await client.patch("/api/v1/auth/me", json={"email": "an@example.com"}, headers=H)
    assert resp.json()["email"] == "an@example.com"
    assert (await client.get("/api/v1/auth/me")).json()["email"] == "an@example.com"
    bad = await client.post(
        "/api/v1/auth/me/password", json={"current_password": "nope", "new_password": "new-password-1"}, headers=H
    )
    assert bad.status_code == 400
    ok = await client.post(
        "/api/v1/auth/me/password", json={"current_password": "analyst", "new_password": "new-password-1"}, headers=H
    )
    assert ok.status_code == 204
    # текущая сессия жива
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    client.cookies.clear()
    assert (await login(client, "analyst", "new-password-1")).status_code == 200
    removed = await client.patch("/api/v1/auth/me", json={"email": None}, headers=H)
    assert removed.json()["email"] is None


async def test_profile_requires_csrf(client):
    await login(client, "analyst", "analyst")
    resp = await client.patch("/api/v1/auth/me", json={"email": "an@example.com"}, headers={"Origin": "http://evil.x"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------- JIT


@pytest.fixture
def mem() -> MemoryUserRepo:
    return MemoryUserRepo()


async def test_jit_creates_user_with_idp_role(mem):
    user = await sso_sign_in(mem, "oidc", "sub-1", "marketer", "Мария", "m@example.com", ["analyst"])
    assert user.provider == "oidc" and user.roles == ["analyst"] and user.email == "m@example.com"
    assert user.last_login_at is not None
    again = await sso_sign_in(mem, "oidc", "sub-1", "marketer", "Мария", None, ["admin"])
    assert again.id == user.id and again.roles == ["admin"]


async def test_jit_without_group_denied(mem):
    with pytest.raises(SsoDenied) as exc:
        await sso_sign_in(mem, "saml", "sub-2", "outsider", "Посторонний", None, [])
    assert exc.value.code == "forbidden"
    assert not mem.users


async def test_blocked_sso_user_cannot_enter(mem):
    user = await sso_sign_in(mem, "saml", "sub-3", "marketer", "Мария", None, ["analyst"])
    await mem.update_user(user.id, is_active=False)
    with pytest.raises(SsoDenied) as exc:
        await sso_sign_in(mem, "saml", "sub-3", "marketer", "Мария", None, ["analyst"])
    assert exc.value.code == "blocked"


async def test_manual_role_survives_idp_groups(mem):
    user = await sso_sign_in(mem, "oidc", "sub-4", "marketer", "Мария", None, ["analyst"])
    await mem.update_user(user.id, roles=["admin"], role_override=True)
    again = await sso_sign_in(mem, "oidc", "sub-4", "marketer", "Мария", None, [])
    assert again.roles == ["admin"]


async def test_pending_sso_user_claimed_on_first_login(mem):
    pending = await mem.create_user(
        UserRecord("", "marketer", "Мария", ["admin"], provider="sso", role_override=True, email="m@corp")
    )
    user = await sso_sign_in(mem, "saml", "sub-5", "marketer", "Мария М.", "other@corp.example", ["analyst"])
    assert user.id == pending.id
    assert user.provider == "saml" and user.subject == "sub-5"
    assert user.roles == ["admin"]


async def test_pending_blocked_user_denied(mem):
    pending = await mem.create_user(UserRecord("", "marketer", "Мария", ["analyst"], provider="sso"))
    await mem.update_user(pending.id, is_active=False)
    with pytest.raises(SsoDenied):
        await sso_sign_in(mem, "oidc", "sub-6", "marketer", "Мария", None, ["analyst"])
