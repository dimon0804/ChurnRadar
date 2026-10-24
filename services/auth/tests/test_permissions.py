"""Три роли: таблица прав шлюза (ForwardAuth), роли в управлении пользователями и из IdP."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.permissions import UNSAFE_RULES, allowed, required_role
from app.roles import map_roles

from .conftest import ORIGIN, login

PASSWORDS = {"admin": "admin", "marketer": "marketer", "analyst": "analyst"}
RANK = {"analyst": 1, "marketer": 2, "admin": 3}

# (метод, путь, минимальная роль) — то же, что в README «Роли и права»
MATRIX = [
    ("GET", "/api/v1/customers", "analyst"),
    ("GET", "/api/v1/customers/export.csv", "analyst"),
    ("GET", "/api/v1/mail/templates", "analyst"),
    ("GET", "/api/v1/model/settings", "analyst"),
    ("PUT", "/api/v1/customers/7/action", "marketer"),
    ("DELETE", "/api/v1/customers/7/action", "marketer"),
    ("POST", "/api/v1/customers/7/email/draft", "marketer"),
    ("POST", "/api/v1/customers/7/email", "marketer"),
    ("POST", "/api/v1/mail/campaigns/preview", "marketer"),
    ("POST", "/api/v1/mail/campaigns", "marketer"),
    ("PUT", "/api/v1/mail/templates/general", "admin"),
    ("POST", "/api/v1/mail/templates/general/reset", "admin"),
    ("PUT", "/api/v1/analytics/marketing/settings", "admin"),
    ("PATCH", "/api/v1/model/settings", "admin"),
    ("POST", "/api/v1/model/settings/preview", "admin"),
    ("POST", "/api/v1/model/settings/reset", "admin"),
    ("PUT", "/api/v1/source/snapshot", "admin"),
    ("POST", "/api/v1/source/events", "admin"),
    ("PUT", "/api/v1/simulator", "admin"),
    ("PUT", "/api/v1/alerts/preferences", "analyst"),
    ("POST", "/api/v1/alerts/preferences/test", "analyst"),
    ("POST", "/api/v1/alerts/notifications/read", "analyst"),
    ("DELETE", "/api/v1/alerts/preferences/users/anna", "admin"),
    ("POST", "/api/v1/auth/users", "admin"),
    ("PATCH", "/api/v1/auth/me", "analyst"),
    ("POST", "/api/v1/unknown/thing", "admin"),
]


@pytest.mark.parametrize(("method", "path", "minimum"), MATRIX)
def test_rule_table(method, path, minimum):
    need = required_role(method, path)
    assert (need or "analyst") == minimum
    for role, rank in RANK.items():
        ok, _ = allowed([role], method, path)
        assert ok is (rank >= RANK[minimum]), (role, method, path)


def test_prefix_boundaries():
    # /api/v1/modelling — не /api/v1/model; без правила изменяющий запрос только для admin
    assert required_role("POST", "/api/v1/modelling") == "admin"
    assert required_role("POST", "/api/v1/mailbox") == "admin"
    assert all(prefix.startswith("/api/v1/") for prefix, _ in UNSAFE_RULES)


def test_unknown_role_has_no_rights():
    assert allowed(["guest"], "PUT", "/api/v1/alerts/preferences") == (False, "analyst")
    assert allowed(["guest"], "GET", "/api/v1/customers") == (True, None)


def forwarded(method, uri):
    return {"X-Forwarded-Method": method, "X-Forwarded-Uri": uri, "X-Forwarded-Proto": "http", "Origin": ORIGIN}


@pytest.mark.parametrize("role", list(PASSWORDS))
async def test_forward_auth_matrix(client, role):
    resp = await login(client, role, PASSWORDS[role])
    assert resp.json()["roles"] == [role]
    for method, path, minimum in MATRIX:
        verdict = await client.get("/internal/verify", headers=forwarded(method, path))
        expected = 200 if RANK[role] >= RANK[minimum] else 403
        assert verdict.status_code == expected, (role, method, path)
        if expected == 200:
            assert verdict.headers["x-user-roles"] == role


async def test_forbidden_message_says_who_can(client):
    await login(client, "analyst", "analyst")
    resp = await client.get("/internal/verify", headers=forwarded("POST", "/api/v1/customers/1/email"))
    assert "маркетологу" in resp.json()["error"]["message"]


async def test_admin_assigns_marketer_role(client, repo):
    await login(client)
    created = await client.post(
        "/api/v1/auth/users",
        json={"username": "masha", "role": "marketer", "password": "masha-password"},
        headers={"Origin": ORIGIN},
    )
    assert created.status_code == 201 and created.json()["user"]["role"] == "marketer"
    client.cookies.clear()
    assert (await login(client, "masha", "masha-password")).json()["roles"] == ["marketer"]


async def test_demoting_admin_to_marketer_keeps_last_admin(client, repo):
    await login(client)
    boss = (
        await client.post(
            "/api/v1/auth/users",
            json={"username": "boss", "role": "admin", "password": "boss-password"},
            headers={"Origin": ORIGIN},
        )
    ).json()["user"]
    admin_id = next(u.id for u in repo.users.values() if u.username == "admin")
    await repo.update_user(admin_id, roles=["marketer"])
    resp = await client.patch(f"/api/v1/auth/users/{boss['id']}", json={"role": "marketer"}, headers={"Origin": ORIGIN})
    assert resp.status_code == 409


def test_groups_map_to_three_roles():
    s = Settings(oidc_enabled=False, saml_enabled=False)
    assert map_roles(s, groups=["churn-marketers"]) == ["marketer"]
    assert map_roles(s, groups=["/churn-analysts"]) == ["analyst"]
    assert map_roles(s, groups=["churn-admins", "churn-marketers"]) == ["admin", "marketer"]
    assert map_roles(s, roles=["Marketer"]) == ["marketer"]
    assert map_roles(s, groups=["staff"]) == []
