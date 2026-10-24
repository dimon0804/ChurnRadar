"""HTTP-ручки настроек уведомлений на настоящем Postgres: свои настройки, тест, личная лента, админ."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from svckit.mail import MailConfig

from app import repository as repo
from app.channels import build_channels
from app.config import Config
from app.delivery import Dispatcher
from app.fanout import Fanout
from app.main import State, create_http_app
from fakes import FakeSmtp, put_summary

pytestmark = pytest.mark.integration

ANNA = {"X-User-Name": "anna", "X-User-Roles": "analyst"}
ADMIN = {"X-User-Name": "admin", "X-User-Roles": "admin"}


async def _app(pool: Any, config: Config) -> tuple[Any, State]:
    state = State()
    state.pool = pool
    return create_http_app(config, state), state


def contract(name: str, body: Any) -> None:
    """Ответ совпадает со схемой из contracts/openapi.yaml."""
    jsonschema = pytest.importorskip("jsonschema")
    yaml = pytest.importorskip("yaml")
    spec = yaml.safe_load((Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml").read_text("utf-8"))
    schema = {"$ref": f"#/components/schemas/{name}", "components": spec["components"]}
    jsonschema.validate(body, schema, cls=jsonschema.Draft202012Validator)


async def call(app: Any, method: str, path: str, headers: dict[str, str], json: Any = None) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://s") as c:
        return await c.request(method, path, headers=headers, json=json)


async def test_preferences_test_message_inbox_and_admin(db_pool: Any, smtp: FakeSmtp) -> None:
    config = Config(public_url="https://churn.example", mail=MailConfig(host="127.0.0.1", port=smtp.port))
    app, _ = await _app(db_pool, config)

    resp = await call(app, "GET", "/api/v1/alerts/preferences", ANNA)
    assert resp.status_code == 200
    body = resp.json()
    contract("AlertPreferencesResponse", body)
    assert body["preferences"]["username"] == "anna"
    assert {e["code"] for e in body["catalog"]} >= {"loss_growth", "big_customer_high", "risk_share"}
    assert body["channels"]["email"]["available"] is True
    assert body["channels"]["telegram"] == {
        "available": False,
        "reason": "Бот не настроен: не задан TELEGRAM_BOT_TOKEN",
        "bot_username": None,
        "linked": False,
        "tg_username": None,
    }

    bad = await call(
        app, "PUT", "/api/v1/alerts/preferences", ANNA, {"events": {"risk_share": {"channels": ["email"]}}}
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["details"][0]["field"] == "email"

    prefs = {
        "email": "anna@example.com",
        "quiet_from": "23:00",
        "quiet_to": "07:00",
        "digest_enabled": True,
        "digest_time": "00:00",
        "digest_channels": ["email"],
        "events": {"risk_share": {"enabled": True, "threshold": 35, "channels": ["panel", "email"]}},
    }
    saved = await call(app, "PUT", "/api/v1/alerts/preferences", ANNA, prefs)
    assert saved.status_code == 200
    assert saved.json()["preferences"]["events"]["risk_share"]["threshold"] == 35
    assert saved.json()["preferences"]["quiet_from"] == "23:00"

    # тестовое уведомление: в панель сразу, письмо — в очередь (тихие часы на него не действуют)
    test = await call(app, "POST", "/api/v1/alerts/preferences/test", ANNA)
    results = {r["channel"]: r for r in test.json()["results"]}
    assert results["panel"]["ok"] and results["email"]["ok"]
    assert results["telegram"] == {"channel": "telegram", "ok": False, "reason": "бот не настроен"}
    dispatcher = Dispatcher(db_pool, build_channels(config, httpx.AsyncClient()))
    assert await dispatcher.run_once() == 1
    assert smtp.messages[0]["to"] == ["anna@example.com"]
    assert smtp.messages[0]["subject"] == "ChurnRadar: Тестовое уведомление"

    inbox = (await call(app, "GET", "/api/v1/alerts/notifications", ANNA)).json()
    contract("NotificationInbox", inbox)
    assert inbox["unread"] == 1 and inbox["items"][0]["link"] == "/notifications"
    assert (await call(app, "POST", "/api/v1/alerts/notifications/read", ANNA)).status_code == 204
    assert (await call(app, "GET", "/api/v1/alerts/notifications", ANNA)).json()["unread"] == 0

    # дайджест: время 00:00 уже наступило — уходит один раз за день, повторный тик ничего не шлёт
    async with db_pool.acquire() as conn:
        await put_summary(conn, loss=50_000, high=2, share=0.3, customers=10)
        fanout = Fanout(config)
        now = datetime.now(UTC)
        assert await fanout.on_tick(conn, now=now)
        assert not await fanout.on_tick(conn, now=now + timedelta(minutes=1))
        payload = await conn.fetchval(
            "SELECT payload FROM alerts.deliveries WHERE status = 'pending' AND username = 'anna'"
        )
    assert payload["subject"] == "ChurnRadar: Сводка ChurnRadar за сутки"
    assert "Клиентов в высоком риске: 2 из 10" in payload["text"]

    # администратор видит чужие настройки и может их сбросить
    listed = (await call(app, "GET", "/api/v1/alerts/preferences/users", ADMIN)).json()["items"]
    anna = next(i for i in listed if i["username"] == "anna")
    assert anna["email"] == "anna@example.com" and anna["digest_enabled"] and "email" in anna["channels"]
    other = await call(app, "GET", "/api/v1/alerts/preferences/users/anna", ADMIN)
    assert other.json()["preferences"]["events"]["risk_share"]["threshold"] == 35
    assert (await call(app, "DELETE", "/api/v1/alerts/preferences/users/anna", ADMIN)).status_code == 204
    assert (await call(app, "DELETE", "/api/v1/alerts/preferences/users/anna", ADMIN)).status_code == 404
    fresh = (await call(app, "GET", "/api/v1/alerts/preferences", ANNA)).json()["preferences"]
    assert fresh["email"] is None and fresh["events"]["risk_share"]["threshold"] == 40


async def test_stale_data_once_until_recovered(db_pool: Any) -> None:
    config = Config()
    app, _ = await _app(db_pool, config)
    await call(
        app,
        "PUT",
        "/api/v1/alerts/preferences",
        ANNA,
        {"events": {"stale_data": {"enabled": True, "threshold": 10, "channels": ["panel"]}}},
    )
    now = datetime.now(UTC)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO source.snapshots (id, kind, created_at) VALUES ('s1', 'changes', $1)",
            now - timedelta(minutes=30),
        )
        await put_summary(conn, loss=1, scored_at=now - timedelta(minutes=40))
        fanout = Fanout(config)
        await fanout.on_tick(conn, now=now)
        await fanout.on_tick(conn, now=now + timedelta(minutes=1))  # всё ещё не пересчитано — второй раз не шлём
        rows = await conn.fetch("SELECT event_type FROM alerts.notifications WHERE username = 'anna'")
        assert [r["event_type"] for r in rows] == ["stale_data"]
        journal = await conn.fetch("SELECT kind FROM alerts.feed")
        assert [r["kind"] for r in journal] == ["stale_data"]
        # пересчёт догнал данные — состояние снято, следующий сбой снова будет замечен
        await put_summary(conn, loss=1, scored_at=now)
        await fanout.on_tick(conn, now=now + timedelta(minutes=2))
        states = await repo.event_states(conn)
        assert states[("anna", "stale_data")][1] is False
