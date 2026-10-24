from __future__ import annotations

import fakeredis
import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.config import Settings
from app.main import create_app
from app.users import MemoryUserRepo

ORIGIN = "http://localhost:8080"


def offline_transport(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503)


@pytest.fixture
def settings() -> Settings:
    return Settings(oidc_enabled=False, log_level="warning")


@pytest.fixture
def repo() -> MemoryUserRepo:
    return MemoryUserRepo()


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def upstream_transport():
    return httpx.MockTransport(offline_transport)


@pytest.fixture
async def client(settings, repo, redis, upstream_transport):
    upstream = httpx.AsyncClient(transport=upstream_transport)
    app = create_app(settings, repo=repo, redis=redis, http=upstream)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, client=("10.0.0.5", 5000))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://auth", headers={"X-Forwarded-Host": "localhost:8080"}
        ) as c:
            yield c
    await upstream.aclose()


async def login(client: httpx.AsyncClient, username="admin", password="admin") -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}, headers={"Origin": ORIGIN}
    )
