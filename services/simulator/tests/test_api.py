from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from app.config import Config
from app.controller import Controller
from app.main import create_app
from app.stream import World

ADMIN = {"X-User-Name": "dima", "X-User-Roles": "admin"}
ANALYST = {"X-User-Name": "ann", "X-User-Roles": "analyst"}


class FakeIngest:
    def __init__(self, status: int = 202) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/snapshot"):
            return httpx.Response(self.status, json={"snapshot_id": "s1", "counts": {}})
        return httpx.Response(self.status, json={"accepted": 1})


def _config(**overrides) -> Config:  # type: ignore[no-untyped-def]
    return replace(Config.from_env(), ingest_url="http://ingest:8002", running=False, auth_dev_user=None, **overrides)


@pytest.fixture
def ingest() -> FakeIngest:
    return FakeIngest()


@pytest.fixture
async def client(ingest):  # type: ignore[no-untyped-def]
    config = _config()
    ingest_client = httpx.AsyncClient(transport=httpx.MockTransport(ingest))
    world = World(random.Random(1), clock=lambda: datetime(2026, 10, 19, 15, tzinfo=UTC))
    controller = Controller(config, ingest_client, world=world)
    app = create_app(config, controller)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim") as c:
            yield c, controller
    await ingest_client.aclose()


async def test_requires_user(client) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    response = await c.get("/api/v1/simulator")
    assert response.status_code == 401
    body = response.json()["error"]
    assert body["code"] == "unauthorized" and body["message"] and body["request_id"]


async def test_get_state(client, openapi_validator) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    response = await c.get("/api/v1/simulator", headers=ANALYST)
    assert response.status_code == 200
    state = response.json()
    assert state["running"] is False and state["events_emitted"] == 0
    assert not list(openapi_validator("SimulatorState").iter_errors(state))


async def test_put_requires_admin(client) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    response = await c.put("/api/v1/simulator", json={"running": True}, headers=ANALYST)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_validation_error_format(client) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    response = await c.put("/api/v1/simulator", json={"interval_ms": 10}, headers=ADMIN)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_generate_puts_snapshot_and_stream_posts_events(client, ingest, openapi_validator) -> None:  # type: ignore[no-untyped-def]
    c, controller = client
    response = await c.put(
        "/api/v1/simulator", json={"generate": {"accounts": 40, "seed": 9}, "interval_ms": 500}, headers=ADMIN
    )
    assert response.status_code == 200, response.text
    assert response.json()["interval_ms"] == 500
    put = ingest.requests[-1]
    assert put.method == "PUT" and put.url.path == "/api/v1/source/snapshot"
    assert put.headers["X-User-Name"] == "simulator" and put.headers["X-User-Roles"] == "admin"
    snapshot = json.loads(put.content)
    assert len(snapshot["accounts"]) == 40
    assert not list(openapi_validator("SourceSnapshot").iter_errors(snapshot))
    assert controller.last_generate["seed"] == 9

    changes = await controller.step()
    assert changes >= 1
    post = ingest.requests[-1]
    assert post.method == "POST" and post.url.path == "/api/v1/source/events"
    assert post.headers["X-User-Roles"] == "admin"
    assert not list(openapi_validator("SourceChangeBatch").iter_errors(json.loads(post.content)))
    state = (await c.get("/api/v1/simulator", headers=ADMIN)).json()
    assert state["events_emitted"] == changes


async def test_running_toggle(client) -> None:  # type: ignore[no-untyped-def]
    c, controller = client
    assert (await c.put("/api/v1/simulator", json={"running": True}, headers=ADMIN)).json()["running"] is True
    assert controller._task is not None
    assert (await c.put("/api/v1/simulator", json={"running": False}, headers=ADMIN)).json()["running"] is False


async def test_ingest_rejects_too_large_snapshot(client, ingest) -> None:  # type: ignore[no-untyped-def]
    c, controller = client
    ingest.status = 413
    response = await c.put("/api/v1/simulator", json={"generate": {"accounts": 5, "seed": 1}}, headers=ADMIN)
    assert response.status_code == 413
    assert "MAX_BODY_BYTES" in response.json()["error"]["message"]
    assert controller.last_error


async def test_metrics_need_token(client) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    assert (await c.get("/metrics")).status_code == 401
    response = await c.get("/metrics", headers={"Authorization": "Bearer churn-metrics-token"})
    assert response.status_code == 200 and "simulator_running" in response.text


async def test_healthz(client) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    assert (await c.get("/healthz")).json() == {"status": "ok"}
