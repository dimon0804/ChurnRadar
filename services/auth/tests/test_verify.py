from __future__ import annotations

import pytest

from .conftest import ORIGIN, login


def forwarded(method="GET", uri="/api/v1/customers", **extra):
    headers = {"X-Forwarded-Method": method, "X-Forwarded-Uri": uri, "X-Forwarded-Proto": "http"}
    headers.update(extra)
    return headers


async def test_verify_without_session_is_401(client):
    resp = await client.get("/internal/verify", headers=forwarded())
    assert resp.status_code == 401
    assert "x-user-id" not in resp.headers


async def test_verify_redirect_mode_for_pages(client):
    resp = await client.get("/internal/verify?redirect=1", headers=forwarded(uri="/customers?sort=email"))
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:8080/login?next=%2Fcustomers%3Fsort%3Demail"


async def test_verify_sets_identity_headers(client):
    await login(client)
    resp = await client.get("/internal/verify", headers=forwarded())
    assert resp.status_code == 200
    assert resp.headers["x-user-name"] == "admin"
    assert resp.headers["x-user-roles"] == "admin"
    assert resp.headers["x-user-id"]


async def test_verify_accepts_bearer(client):
    token = (await login(client)).cookies["cr_session"]
    client.cookies.clear()
    resp = await client.get("/internal/verify", headers=forwarded(Authorization=f"Bearer {token}"))
    assert resp.status_code == 200


async def test_unsafe_cookie_request_needs_same_origin(client):
    await login(client)
    no_origin = await client.get("/internal/verify", headers=forwarded("PATCH", "/api/v1/model/settings"))
    assert no_origin.status_code == 403
    assert no_origin.json()["error"]["code"] == "csrf_rejected"

    foreign = await client.get(
        "/internal/verify", headers=forwarded("PATCH", "/api/v1/model/settings", Origin="http://evil.test")
    )
    assert foreign.status_code == 403

    ok = await client.get("/internal/verify", headers=forwarded("PATCH", "/api/v1/model/settings", Origin=ORIGIN))
    assert ok.status_code == 200


async def test_bearer_unsafe_request_skips_origin_check(client):
    token = (await login(client)).cookies["cr_session"]
    client.cookies.clear()
    resp = await client.get(
        "/internal/verify", headers=forwarded("PUT", "/api/v1/source/snapshot", Authorization=f"Bearer {token}")
    )
    assert resp.status_code == 200


@pytest.mark.parametrize(
    ("method", "uri"),
    [
        ("PATCH", "/api/v1/model/settings"),
        ("PUT", "/api/v1/source/snapshot"),
        ("POST", "/api/v1/model/settings/reset"),
        ("PUT", "/api/v1/simulator"),
    ],
)
async def test_analyst_cannot_change_model_or_data(client, method, uri):
    await login(client, "analyst", "analyst")
    resp = await client.get("/internal/verify", headers=forwarded(method, uri, Origin=ORIGIN))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_analyst_can_read(client):
    await login(client, "analyst", "analyst")
    resp = await client.get("/internal/verify", headers=forwarded("GET", "/api/v1/model/settings"))
    assert resp.status_code == 200
    assert resp.headers["x-user-roles"] == "analyst"


async def test_verify_metrics_token(client, settings):
    assert (await client.get("/internal/verify-metrics")).status_code == 401
    bad = await client.get("/internal/verify-metrics", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401
    ok = await client.get("/internal/verify-metrics", headers={"Authorization": f"Bearer {settings.metrics_token}"})
    assert ok.status_code == 200


async def test_probes_and_metrics(client):
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 200
    await login(client)
    body = (await client.get("/metrics")).text
    assert 'auth_logins_total{method="password",result="success"}' in body
