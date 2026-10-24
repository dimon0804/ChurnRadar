from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app import model_catalog
from app.main import create_app

ADMIN = {"X-User-Id": "1", "X-User-Name": "admin", "X-User-Roles": "admin"}
ANALYST = {"X-User-Id": "2", "X-User-Name": "analyst", "X-User-Roles": "analyst"}
MARKETER = {"X-User-Id": "3", "X-User-Name": "marketer", "X-User-Roles": "marketer"}

FAKE_FEATURES = [
    ("prolong_wait", 85.0, "service"),
    ("autoprolong_off_expiring", 60.0, "service"),
    ("churn_history", 40.0, "account"),
]


def _fake_riskcore() -> SimpleNamespace:
    """Минимальная замена riskcore: те же сигнатуры, фиксированный каталог из трёх признаков."""

    def default_settings():
        return SimpleNamespace(
            version=1,
            horizon_days=30,
            thresholds={"medium": 35.0, "high": 65.0},
            weights={code: {"weight": w, "enabled": True} for code, w, _ in FAKE_FEATURES},
        )

    def merge_settings(stored, version=None):
        base = default_settings()
        stored = stored or {}
        weights = dict(base.weights)
        for code, entry in (stored.get("weights") or {}).items():
            if code in weights:
                weights[code] = {**weights[code], **entry}
        return SimpleNamespace(
            version=version if version is not None else stored.get("version", 1),
            horizon_days=stored.get("horizon_days", base.horizon_days),
            thresholds={**base.thresholds, **(stored.get("thresholds") or {})},
            weights=weights,
        )

    def settings_to_api(s, updated_at, updated_by):
        return {
            "version": s.version,
            "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at,
            "updated_by": updated_by,
            "horizon_days": s.horizon_days,
            "thresholds": dict(s.thresholds),
            "weights": [
                {
                    "code": code, "title": code, "description": "", "group": "renewal", "level": level,
                    "weight": s.weights[code]["weight"], "default_weight": w, "min": 0.0, "max": 100.0,
                    "enabled": s.weights[code]["enabled"],
                }
                for code, w, level in FAKE_FEATURES
            ],
        }

    return SimpleNamespace(
        default_settings=default_settings, merge_settings=merge_settings, settings_to_api=settings_to_api
    )


@pytest.fixture
def fake_riskcore(monkeypatch):
    fake = _fake_riskcore()
    monkeypatch.setattr(model_catalog, "_rc", lambda: fake)
    return fake


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict]] = []
        self.connected = True

    async def publish(self, subject, event_type, payload, retries=3):
        self.published.append((subject, event_type, payload))
        return {"payload": payload}


class FakeConn:
    """Отвечает на запросы по подстроке SQL; неизвестный запрос — ошибка теста."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.queries: list[str] = []

    def _answer(self, sql: str):
        self.queries.append(sql)
        for key, value in self.answers.items():
            if key in sql:
                return value
        raise AssertionError(f"неожиданный запрос: {sql}")

    async def fetchrow(self, sql, *args):
        return self._answer(sql)

    async def fetch(self, sql, *args):
        return self._answer(sql)

    async def fetchval(self, sql, *args):
        return self._answer(sql)


class FakePool:
    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.conn = FakeConn(answers or {})

    def acquire(self, timeout=None):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.fixture
def app(fake_riskcore):
    application = create_app(with_lifespan=False)
    application.state.pool = FakePool()
    application.state.bus = FakeBus()
    return application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


NOW = datetime(2026, 10, 19, 15, tzinfo=UTC)
