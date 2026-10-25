"""Матрица прав: роль × изменяющий эндпоинт api.

Роль проверяется зависимостью раньше, чем валидируется тело и идёт запрос в базу, поэтому
запрещённая роль всегда получает 403, а разрешённая — что угодно, кроме 401/403 (422 на
пустом теле, 404/503 на фейковой базе — тоже «пропустили»).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import ADMIN, ANALYST, MARKETER

ROLES = {"analyst": ANALYST, "marketer": MARKETER, "admin": ADMIN}
RANK = {"analyst": 1, "marketer": 2, "admin": 3}

# (метод, путь, тело, минимальная роль)
ENDPOINTS = [
    # работа с клиентами: отметки, письма, рассылки
    ("PUT", "/api/v1/customers/abc/action", {}, "marketer"),
    ("DELETE", "/api/v1/customers/abc/action", None, "marketer"),
    ("POST", "/api/v1/customers/abc/email/draft", {}, "marketer"),
    ("POST", "/api/v1/customers/abc/email", {}, "marketer"),
    ("POST", "/api/v1/mail/campaigns/preview", {"limit": "много"}, "marketer"),
    ("POST", "/api/v1/mail/campaigns", {"limit": "много"}, "marketer"),
    # настройка системы
    ("PUT", "/api/v1/mail/templates/general", {}, "admin"),
    ("POST", "/api/v1/mail/templates/general/reset", None, "admin"),
    ("PUT", "/api/v1/analytics/marketing/settings", {"margin_pct": "x"}, "admin"),
    ("PATCH", "/api/v1/model/settings", {"horizon_days": "x"}, "admin"),
    ("POST", "/api/v1/model/settings/preview", {"horizon_days": "x"}, "admin"),
    ("POST", "/api/v1/model/settings/reset", None, "admin"),
    ("POST", "/api/v1/model/settings/rollback", {"version": 0}, "admin"),
    ("PUT", "/api/v1/simulator", None, "admin"),
]

CASES = [
    pytest.param(
        method,
        path,
        body,
        role,
        RANK[role] >= RANK[minimum],
        id=f"{role}-{method}-{path}",
    )
    for method, path, body, minimum in ENDPOINTS
    for role in ROLES
]


@pytest.fixture
async def lenient_client(app):
    # разрешённой роли фейковая база может ответить чем угодно, вплоть до 500 — нам важно лишь «не 403»
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.parametrize(("method", "path", "body", "role", "allowed"), CASES)
async def test_role_matrix(lenient_client, method, path, body, role, allowed):
    resp = await lenient_client.request(method, path, json=body, headers=ROLES[role])
    if allowed:
        assert resp.status_code not in (401, 403), resp.text
    else:
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize(("method", "path", "body", "minimum"), ENDPOINTS)
async def test_anonymous_is_401(lenient_client, method, path, body, minimum):
    resp = await lenient_client.request(method, path, json=body)
    assert resp.status_code == 401


async def test_forbidden_message_names_the_role(lenient_client):
    resp = await lenient_client.post(
        "/api/v1/customers/1/email", json={}, headers=ANALYST
    )
    assert "маркетологу" in resp.json()["error"]["message"]
    resp = await lenient_client.patch(
        "/api/v1/model/settings", json={}, headers=MARKETER
    )
    assert "администратору" in resp.json()["error"]["message"]


@pytest.mark.parametrize("role", list(ROLES))
async def test_everyone_reads_templates_and_exports(lenient_client, role):
    for path in ("/api/v1/mail/templates", "/api/v1/customers/export.csv"):
        resp = await lenient_client.get(path, headers=ROLES[role])
        assert resp.status_code not in (401, 403), path
