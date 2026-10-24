from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_PATH = REPO_ROOT / "data" / "sample" / "data.json"

ADMIN = {"X-User-Id": "1", "X-User-Name": "admin", "X-User-Roles": "admin"}
ANALYST = {"X-User-Id": "2", "X-User-Name": "analyst", "X-User-Roles": "analyst"}


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict]] = []
        self.connected = True

    async def publish(self, subject, event_type, payload, retries=3):
        self.published.append((subject, event_type, payload))
        return {"event_id": "x", "payload": payload}


@pytest.fixture(scope="session")
def sample() -> dict:
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def snapshot(sample) -> dict:
    return copy.deepcopy(sample)


@pytest.fixture
def app():
    application = create_app(with_lifespan=False)
    # пул-заглушка: unit-тесты не доходят до БД
    application.state.pool = object()
    application.state.bus = FakeBus()
    return application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
