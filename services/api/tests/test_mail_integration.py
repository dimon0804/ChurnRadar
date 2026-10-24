"""Письма клиентам на настоящем Postgres и фейковом SMTP: одно письмо, рассылка, эффективность."""

from __future__ import annotations

import asyncio

import asyncpg
import nats
import pytest
from httpx import ASGITransport, AsyncClient
from nats.js.errors import NotFoundError

from app.config import settings
from app.main import create_app
from tests.conftest import ADMIN, ANALYST
from tests.fake_smtp import FakeSMTP
from tests.test_integration import DB_URL, NATS_URL, insert_scores, reset_database

pytestmark = pytest.mark.integration

MANAGER = {**ANALYST, "X-User-Email": "anna@corp.test"}


@pytest.fixture
async def env(monkeypatch):
    if not DB_URL or not NATS_URL:
        pytest.skip("нужны TEST_DATABASE_URL и TEST_NATS_URL")
    try:
        await reset_database(DB_URL)
        nc = await nats.connect(NATS_URL)
        try:
            await nc.jetstream().purge_stream("CHURN")
        except NotFoundError:
            pass
        await nc.close()
    except (OSError, asyncpg.PostgresError, nats.errors.NoServersError) as exc:
        pytest.skip(f"Postgres/NATS недоступны: {exc}")
    async with FakeSMTP() as smtp:
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(smtp.port))
        monkeypatch.setenv("SMTP_FROM", "care@hosting.test")
        monkeypatch.setenv("SMTP_FROM_NAME", "Хостинг")
        monkeypatch.setattr(settings, "database_url", DB_URL)
        monkeypatch.setattr(settings, "nats_url", NATS_URL)
        monkeypatch.setattr(settings, "summary_cache_ttl_s", 0.0)
        monkeypatch.setattr(settings, "mail_rate_per_s", 50.0)
        monkeypatch.setattr(settings, "mail_poll_interval_s", 0.2)
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await insert_scores(DB_URL)
                yield client, app, smtp


async def _sql(query: str, *args):
    conn = await asyncpg.connect(DB_URL)
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


async def test_templates_are_editable_by_admin(env):
    client, _, _ = env
    body = (await client.get("/api/v1/mail/templates", headers=ANALYST)).json()
    codes = [t["code"] for t in body["items"]]
    assert codes[0] == "insufficient_funds" and "general" in codes
    assert all(t["is_default"] for t in body["items"])
    assert {p["name"] for p in body["placeholders"]} >= {
        "email",
        "service",
        "date",
        "amount",
        "balance",
    }

    bad = await client.put(
        "/api/v1/mail/templates/general",
        json={"subject": "{{who}}", "body": "x"},
        headers=ADMIN,
    )
    assert bad.status_code == 422
    ok = await client.put(
        "/api/v1/mail/templates/general",
        json={"subject": "Вопрос по {{service}}", "body": "Текст"},
        headers=ADMIN,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["is_default"] is False and ok.json()["updated_by"] == "admin"
    assert (
        await client.put(
            "/api/v1/mail/templates/nope",
            json={"subject": "s", "body": "b"},
            headers=ADMIN,
        )
    ).status_code == 404
    reset = (
        await client.post("/api/v1/mail/templates/general/reset", headers=ADMIN)
    ).json()
    assert reset["is_default"] is True


async def test_send_one_email_marks_customer(env):
    client, app, smtp = env
    draft = (
        await client.post("/api/v1/customers/1/email/draft", json={}, headers=MANAGER)
    ).json()
    # у клиента главная причина prolong_wait — берём её шаблон
    assert draft["template_code"] == "prolong_wait"
    assert draft["to"] == "anna@example.test" and draft["reply_to"] == "anna@corp.test"
    assert draft["subject"] == "service-1 ждёт оплаты"
    assert draft["last_sent_at"] is None and "{{" not in draft["text"]
    other = (
        await client.post(
            "/api/v1/customers/1/email/draft",
            json={"template_code": "general"},
            headers=MANAGER,
        )
    ).json()
    assert (
        other["template_code"] == "general"
        and other["subject"] == "Нужна помощь с service-1?"
    )

    sent = await client.post(
        "/api/v1/customers/1/email",
        json={
            "template_code": draft["template_code"],
            "subject": draft["subject"],
            "text": draft["text"] + "\n\nP.S. Для {{email}}",
        },
        headers=MANAGER,
    )
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["email"]["status"] == "sent" and body["email"]["sent_at"].endswith("Z")
    assert (
        body["state"]["action"]["status"] == "emailed"
        and body["state"]["action"]["active"] is True
    )
    entry = body["state"]["history"][0]
    assert entry["status"] == "emailed" and entry["note"] == "service-1 ждёт оплаты"
    assert entry["changed_by"] == "analyst" and entry["email_id"] == body["email"]["id"]

    [msg] = smtp.messages
    assert msg["To"] == "anna@example.test" and msg["Reply-To"] == "anna@corp.test"
    assert (
        msg["Subject"] == "service-1 ждёт оплаты" and "care@hosting.test" in msg["From"]
    )
    assert msg["X-ChurnRadar-Customer"] == "1"
    text = msg.get_body(("plain",)).get_content()
    assert (
        "P.S. Для anna@example.test" in text
    )  # подстановки в правленом тексте тоже заполняются

    # «Письмо отправлено» убирает клиента из очереди, ждём ответа
    queue = (await client.get("/api/v1/contact-today?limit=10", headers=ANALYST)).json()
    assert 1 not in [i["id"] for i in queue["items"]] and queue["marked_total"] == 1
    # ответа нет за срок ожидания — клиент возвращается с пометкой
    await _sql(
        "UPDATE scoring.customer_actions SET snoozed_until = now() - interval '1 second' WHERE customer_id = 1"
    )
    queue = (await client.get("/api/v1/contact-today?limit=10", headers=ANALYST)).json()
    first = queue["items"][0]
    assert first["id"] == 1 and first["action"]["return_reason"] == "no_reply"
    assert (
        await client.post("/api/v1/customers/1/email/draft", json={}, headers=ANALYST)
    ).json()["last_sent_at"]

    # SMTP не отвечает — 502, письмо помечено как неотправленное, статус клиента не меняется
    app.state.mail_config = type(app.state.mail_config)(
        host="127.0.0.1", port=1, timeout_s=2
    )
    failed = await client.post(
        "/api/v1/customers/3/email", json={"subject": "s", "text": "t"}, headers=ANALYST
    )
    assert failed.status_code == 502 and failed.json()["error"]["code"] == "mail_failed"
    rows = await _sql(
        "SELECT status, error FROM scoring.customer_emails WHERE customer_id = 3"
    )
    assert rows[0]["status"] == "failed" and rows[0]["error"]
    assert (await client.get("/api/v1/customers/3/action", headers=ANALYST)).json()[
        "action"
    ] is None


async def _wait_done(client, campaign_id: int) -> dict:
    for _ in range(100):
        body = (
            await client.get(f"/api/v1/mail/campaigns/{campaign_id}", headers=ANALYST)
        ).json()
        if body["status"] == "done":
            return body
        await asyncio.sleep(0.1)
    raise AssertionError(f"рассылка не закончилась: {body}")


async def test_campaign_in_background_with_dedup_and_stats(env):
    client, _, smtp = env
    # клиенту 7 недавно писали — его рассылка пропустит
    one = await client.post(
        "/api/v1/customers/7/email", json={"subject": "s", "text": "t"}, headers=ANALYST
    )
    assert one.status_code == 200

    preview = (
        await client.post(
            "/api/v1/mail/campaigns/preview",
            json={"customer_ids": [7, 3, 999]},
            headers=ANALYST,
        )
    ).json()
    assert (
        preview["to_send"] == 1
        and preview["skipped_recent"] == 1
        and preview["not_found"] == 1
    )
    assert (
        preview["sample"]["customer_id"] == 3
        and preview["sample"]["template_code"] == "prolong_wait"
    )

    queue = (
        await client.post(
            "/api/v1/mail/campaigns/preview", json={"worklist": True}, headers=ANALYST
        )
    ).json()
    assert queue["to_send"] == 4 and queue["templates"] == [
        {"code": "prolong_wait", "count": 4}
    ]  # 1, 4, 3, 5

    created = await client.post(
        "/api/v1/mail/campaigns", json={"worklist": True}, headers=ANALYST
    )
    assert created.status_code == 202, created.text
    done = await _wait_done(client, created.json()["id"])
    assert (
        done["sent"] == 4
        and done["failed"] == 0
        and done["queued"] == 0
        and done["created_by"] == "analyst"
    )
    assert sorted(m["To"] for m in smtp.messages) == sorted(
        [
            "galina@other.test",
            "anna@example.test",
            "dmitry@example.test",
            "clara@example.test",
            "eva_x@example.test",
        ]
    )
    queue = (await client.get("/api/v1/contact-today", headers=ANALYST)).json()
    assert queue["items"] == [] and queue["marked_total"] == 5

    again = await client.post(
        "/api/v1/mail/campaigns", json={"customer_ids": [1, 3]}, headers=ANALYST
    )
    assert (
        again.status_code == 422 and again.json()["error"]["code"] == "nothing_to_send"
    )
    forced = await client.post(
        "/api/v1/mail/campaigns",
        json={"customer_ids": [1], "skip_days": 0},
        headers=ANALYST,
    )
    assert forced.status_code == 202
    await _wait_done(client, forced.json()["id"])
    listed = (await client.get("/api/v1/mail/campaigns", headers=ANALYST)).json()[
        "items"
    ]
    assert [c["id"] for c in listed] == [forced.json()["id"], created.json()["id"]]

    stats = (await client.get("/api/v1/mail/stats", headers=ANALYST)).json()
    assert stats["sent"] == 6 and stats["customers"] == 5 and stats["helped"] == 0
    # после писем клиент 3 пополнил баланс, у клиента 4 снизился риск
    await _sql(
        "UPDATE scoring.customer_scores SET balance = balance + 1000 WHERE customer_id = 3"
    )
    await _sql(
        "UPDATE scoring.customer_scores SET risk_score = risk_score - 20 WHERE customer_id = 4"
    )
    stats = (await client.get("/api/v1/mail/stats", headers=ANALYST)).json()
    assert (stats["helped"], stats["topped_up"], stats["risk_down"]) == (2, 1, 1)
    assert stats["helped_share"] == 0.4

    metrics = await client.get(
        "/metrics", headers={"Authorization": f"Bearer {settings.metrics_token}"}
    )
    assert (
        "churn_emails_sent 6.0" in metrics.text
        and "churn_emails_helped 2.0" in metrics.text
    )
