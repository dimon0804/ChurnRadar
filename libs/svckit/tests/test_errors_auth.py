"""Формат ошибок, request_id, пользователь из заголовков, токен метрик."""

# без __future__: Depends из замыкания build_app должен быть объектом
from dataclasses import dataclass
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from prometheus_client import CollectorRegistry, Counter

from svckit.auth import User, make_current_user, make_require_admin, make_require_role, parse_roles
from svckit.errors import ApiError, RequestIdMiddleware, install_error_handlers
from svckit.metrics import (
    HttpMetricsMiddleware,
    bearer_token_valid,
    check_metrics_token,
    http_duration_histogram,
    render,
)


@dataclass
class Settings:
    auth_trust_headers: bool = True
    auth_dev_user: str | None = None
    auth_dev_roles: str = "admin"


ADMIN = {"X-User-Id": "1", "X-User-Name": "admin", "X-User-Roles": "Admin"}
ANALYST = {"X-User-Name": "ann", "X-User-Roles": "analyst"}


def build_app(settings: Settings, registry: CollectorRegistry) -> FastAPI:
    current_user = make_current_user(settings)
    admin = make_require_admin(current_user)
    hits = Counter("hits_total", "hits", registry=registry)
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(HttpMetricsMiddleware, histogram=http_duration_histogram(registry))
    app.add_middleware(RequestIdMiddleware)

    @app.get("/me")
    async def me(user: Annotated[User, Depends(current_user)]) -> dict[str, object]:
        hits.inc()
        return {"id": user.id, "name": user.name, "roles": sorted(user.roles)}

    @app.post("/admin")
    async def only_admin(user: Annotated[User, Depends(admin)]) -> dict[str, str]:
        return {"name": user.name}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("x")

    @app.get("/conflict")
    async def conflict() -> None:
        raise ApiError(409, "conflict", "Уже есть", [{"field": "a"}])

    @app.get("/metrics")
    async def metrics(request: Request):  # type: ignore[no-untyped-def]
        check_metrics_token(request, "tok")
        return render(registry)

    return app


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
async def client(settings):
    app = build_app(settings, CollectorRegistry())
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        yield c


def assert_error(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    err = body["error"]
    assert err["code"] == code
    assert err["message"]
    assert err["request_id"] == resp.headers["x-request-id"]
    return err


async def test_user_from_headers(client):
    resp = await client.get("/me", headers=ADMIN)
    assert resp.json() == {"id": "1", "name": "admin", "roles": ["admin"]}


async def test_unauthorized(client):
    err = assert_error(await client.get("/me"), 401, "unauthorized")
    assert err["message"] == "Требуется вход в систему"


async def test_forbidden(client):
    assert_error(await client.post("/admin", headers=ANALYST), 403, "forbidden")
    assert (await client.post("/admin", headers=ADMIN)).status_code == 200


async def test_dev_user_is_read_on_each_request(client, settings):
    settings.auth_dev_user = "dev"
    resp = await client.post("/admin")
    assert resp.json() == {"name": "dev"}


async def test_headers_ignored_when_not_trusted(client, settings):
    settings.auth_trust_headers = False
    assert_error(await client.get("/me", headers=ADMIN), 401, "unauthorized")


async def test_request_id_echo_and_formats(client):
    resp = await client.get("/nope", headers={"X-Request-Id": "rid-1"})
    assert assert_error(resp, 404, "not_found")["request_id"] == "rid-1"
    err = assert_error(await client.get("/conflict"), 409, "conflict")
    assert err["details"] == [{"field": "a"}]
    assert_error(await client.get("/boom"), 500, "internal_error")
    assert_error(await client.delete("/me"), 405, "method_not_allowed")


async def test_metrics_token(client):
    assert_error(await client.get("/metrics"), 401, "unauthorized")
    assert_error(await client.get("/metrics", headers={"Authorization": "Basic tok"}), 401, "unauthorized")
    await client.get("/me", headers=ADMIN)
    ok = await client.get("/metrics", headers={"Authorization": "bearer  tok "})
    assert ok.status_code == 200
    assert "hits_total 1.0" in ok.text
    assert 'http_request_duration_seconds_count{method="GET",route="/me",status="200"}' in ok.text


def test_bearer_exact_mode():
    assert bearer_token_valid("Bearer t", "t", exact=True)
    assert not bearer_token_valid("bearer t", "t", exact=True)
    assert not bearer_token_valid(None, "t", exact=True)
    assert bearer_token_valid("BEARER t", "t")
    assert not bearer_token_valid("Bearer x", "t")


def test_parse_roles():
    assert parse_roles(" Admin, analyst ,,") == frozenset({"admin", "analyst"})
    assert parse_roles("Admin", lower=False) == frozenset({"Admin"})
    assert parse_roles(None) == frozenset()


def test_api_error_str():
    exc = ApiError(400, "bad_request", "Плохо")
    assert str(exc) == "Плохо"
    assert exc.status_code == 400 and exc.status == 400


@pytest.mark.parametrize(
    ("roles", "minimum", "allowed"),
    [
        ("analyst", "analyst", True),
        ("analyst", "marketer", False),
        ("analyst", "admin", False),
        ("marketer", "analyst", True),
        ("marketer", "marketer", True),
        ("marketer", "admin", False),
        ("admin", "marketer", True),
        ("admin", "admin", True),
        ("analyst,marketer", "marketer", True),
        ("", "analyst", False),
        ("guest", "analyst", False),
    ],
)
async def test_require_role_hierarchy(roles, minimum, allowed):
    current_user = make_current_user(Settings())
    app = FastAPI()
    install_error_handlers(app)
    guard = make_require_role(current_user, minimum)

    @app.post("/x")
    async def act(user: Annotated[User, Depends(guard)]) -> dict[str, str]:
        return {"name": user.name}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post("/x", headers={"X-User-Name": "u", "X-User-Roles": roles})
    assert resp.status_code == (200 if allowed else 403)
    if not allowed:
        assert resp.json()["error"]["code"] == "forbidden"


def test_require_role_rejects_unknown_role():
    with pytest.raises(ValueError):
        make_require_role(make_current_user(Settings()), "boss")
