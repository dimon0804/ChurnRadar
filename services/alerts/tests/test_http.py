"""HTTP: пробы, токен метрик и лента (repository подменён)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app import main as main_mod
from app.config import Config
from app.main import State, create_http_app

USER = {"X-User-Name": "analyst", "X-User-Id": "2", "X-User-Roles": "analyst"}


async def _get(app, path: str, headers: dict[str, str] | None = None) -> httpx.Response:  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://s") as c:
        return await c.get(path, headers=headers)


async def test_probes_and_metrics_token() -> None:
    state = State()
    app = create_http_app(Config(metrics_token="tok"), state)
    assert (await _get(app, "/healthz")).json() == {"status": "ok"}
    resp = await _get(app, "/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"] == {"database": False, "nats": False, "consumer": False, "background_tasks": True}

    assert (await _get(app, "/metrics")).status_code == 401
    assert (await _get(app, "/metrics", {"Authorization": "Bearer nope"})).status_code == 401
    ok = await _get(app, "/metrics", {"Authorization": "Bearer tok"})
    assert ok.status_code == 200
    assert 'alerts_sent_total{channel="webhook"}' in ok.text
    assert "alerts_failed_total" in ok.text


async def test_feed_requires_user() -> None:
    app = create_http_app(Config(), State())
    resp = await _get(app, "/api/v1/alerts")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_feed_list(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def list_feed(pool: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], int, int]:
        calls.append(kwargs)
        rows = [
            {
                "id": 9,
                "kind": "high_risk",
                "customer_id": 42,
                "email": "a@b.c",
                "risk_level": "high",
                "prev_level": "medium",
                "expected_loss": 9000,
                "prev_loss": 1000,
                "message": "m",
                "created_at": datetime(2026, 10, 19, 15, tzinfo=UTC),
            },
            {
                "id": 8,
                "kind": "total_loss_jump",
                "customer_id": None,
                "email": None,
                "risk_level": None,
                "prev_level": None,
                "expected_loss": 5,
                "prev_loss": 4,
                "message": "t",
                "created_at": None,
            },
        ]
        return rows, 31, 9

    monkeypatch.setattr(main_mod.repo, "list_feed", list_feed)
    state = State()
    state.pool = SimpleNamespace()
    app = create_http_app(Config(webhook_url="http://h"), state)
    resp = await _get(app, "/api/v1/alerts?page=2&page_size=2&kind=high_risk", USER)
    assert resp.status_code == 200
    body = resp.json()
    assert calls == [{"page": 2, "page_size": 2, "customer_id": None, "kind": "high_risk"}]
    assert body["total"] == 31 and body["latest_id"] == 9 and body["page"] == 2
    assert body["channels"] == {"feed": True, "webhook": True, "telegram": False}
    assert body["items"][0]["link"] == "/customers/42"
    assert body["items"][0]["created_at"] == "2026-10-19T15:00:00.000Z"
    assert body["items"][1]["link"] == "/customers?level=high"

    assert (await _get(app, "/api/v1/alerts?page_size=500", USER)).status_code == 422
    assert (await _get(app, "/api/v1/alerts?kind=other", USER)).status_code == 422
