"""POST /api/v1/model/settings/preview: проверка патча, права и прокси в scoring (без БД)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.conftest import ADMIN, ANALYST, NOW, FakePool
from tests.test_unit import assert_error

PATH = "/api/v1/model/settings/preview"
SETTINGS_ROW = {"version": 3, "data": None, "updated_at": NOW, "updated_by": "admin"}
PREVIEW = {
    "model_version": 3,
    "exact": True,
    "customers_total": 10,
    "sample_size": 10,
    "before": {"expected_loss": 100},
    "after": {"expected_loss": 150},
    "changes_total": 0,
    "changes": [],
}


@pytest.fixture
def scoring(app):
    seen: dict = {}

    def install(handler=None):
        def default(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=PREVIEW)

        app.state.scoring_http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler or default), base_url="http://scoring:8000"
        )
        return seen

    app.state.pool = FakePool({"FROM scoring.settings": SETTINGS_ROW})
    return install


async def test_requires_admin(client):
    assert_error(await client.post(PATH, json={"horizon_days": 10}), 401, "unauthorized")
    assert_error(await client.post(PATH, json={"horizon_days": 10}, headers=ANALYST), 403, "forbidden")


async def test_sends_patched_settings_to_scoring(client, app, scoring):
    seen = scoring()
    body = {"weights": [{"code": "prolong_wait", "weight": 20}], "thresholds": {"high": 70}}
    resp = await client.post(PATH, json=body, headers=ADMIN)
    assert resp.status_code == 200, resp.text
    assert resp.json() == PREVIEW
    assert seen["path"] == "/internal/v1/model/preview"
    sent = seen["body"]["settings"]
    assert sent["weights"]["prolong_wait"] == {"weight": 20.0, "enabled": True}
    assert sent["weights"]["churn_history"] == {"weight": 40.0, "enabled": True}
    assert sent["thresholds"] == {"medium": 35.0, "high": 70.0}
    assert sent["horizon_days"] == 30
    # пользователь берётся из проверенной сессии, а не пробрасывается как есть
    assert seen["headers"]["x-user-name"] == "admin"
    assert seen["headers"]["x-user-roles"] == "admin"
    assert seen["headers"]["x-request-id"] == resp.headers["x-request-id"]
    # предпросмотр не пишет: ни одного UPDATE/INSERT
    assert all(not q.lstrip().upper().startswith(("UPDATE", "INSERT")) for q in app.state.pool.conn.queries)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"thresholds": {"medium": 70}}, "thresholds"),
        ({"weights": [{"code": "no_such_feature", "weight": 5}]}, "weights.0.code"),
    ],
)
async def test_validation_like_patch(client, scoring, body, field):
    seen = scoring()
    err = assert_error(await client.post(PATH, json=body, headers=ADMIN), 422, "validation_error")
    assert err["details"][0]["field"] == field
    assert "body" not in seen  # до scoring не дошло


async def test_scoring_errors_pass_through(client, scoring):
    scoring(lambda request: httpx.Response(409, json={"error": {"code": "preview_superseded", "message": "x"}}))
    resp = await client.post(PATH, json={"horizon_days": 10}, headers=ADMIN)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "preview_superseded"


async def test_scoring_unavailable_and_timeout(client, scoring):
    def refused(request):
        raise httpx.ConnectError("refused")

    def slow(request):
        raise httpx.ReadTimeout("slow")

    scoring(refused)
    assert_error(await client.post(PATH, json={"horizon_days": 10}, headers=ADMIN), 503, "scoring_unavailable")
    scoring(slow)
    assert_error(await client.post(PATH, json={"horizon_days": 10}, headers=ADMIN), 504, "preview_timeout")
