"""HTTP-часть воркера: пробы и токен метрик (без БД и NATS)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from app.config import Config
from app.main import State, create_http_app


async def _get(app, path: str, headers: dict[str, str] | None = None) -> httpx.Response:  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://s") as c:
        return await c.get(path, headers=headers)


class _Pool:
    def acquire(self, timeout=None):  # type: ignore[no-untyped-def]
        class _Ctx:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                return SimpleNamespace(fetchval=_one)

            async def __aexit__(self, *exc):  # type: ignore[no-untyped-def]
                return False

        return _Ctx()


async def _one(sql: str) -> int:
    return 1


async def test_probes() -> None:
    state = State()
    app = create_http_app(Config.from_env(), state)
    assert (await _get(app, "/healthz")).json() == {"status": "ok"}
    resp = await _get(app, "/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"] == {"database": False, "nats": False, "consumer": False, "initial_scoring": False}

    state.pool = _Pool()
    state.worker = SimpleNamespace(nats_connected=True, subscribed=True)  # type: ignore[assignment]
    state.ready = True
    resp = await _get(app, "/readyz")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


async def test_metrics_token_is_exact() -> None:
    app = create_http_app(Config.from_env(), State())
    resp = await _get(app, "/metrics")
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "unauthorized"
    assert (await _get(app, "/metrics", {"Authorization": "bearer churn-metrics-token"})).status_code == 401
    ok = await _get(app, "/metrics", {"Authorization": "Bearer churn-metrics-token"})
    assert ok.status_code == 200 and "scoring_runs_total" in ok.text
