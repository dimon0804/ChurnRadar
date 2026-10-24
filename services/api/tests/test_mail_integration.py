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
from tests.conftest import ADMIN, ANALYST, MARKETER
from tests.fake_smtp import FakeSMTP
from tests.test_integration import DB_URL, NATS_URL, insert_scores, reset_database

pytestmark = pytest.mark.integration

MANAGER = {**MARKETER, "X-User-Email": "anna@corp.test"}


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
    assert entry["changed_by"] == "marketer" and entry["email_id"] == body["email"]["id"]

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
        await client.post("/api/v1/customers/1/email/draft", json={}, headers=MARKETER)
    ).json()["last_sent_at"]

    # SMTP не отвечает — 502, письмо помечено как неотправленное, статус клиента не меняется
    app.state.mail_config = type(app.state.mail_config)(
        host="127.0.0.1", port=1, timeout_s=2
    )
    failed = await client.post(
        "/api/v1/customers/3/email", json={"subject": "s", "text": "t"}, headers=MARKETER
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
        "/api/v1/customers/7/email", json={"subject": "s", "text": "t"}, headers=MARKETER
    )
    assert one.status_code == 200

    preview = (
        await client.post(
            "/api/v1/mail/campaigns/preview",
            json={"customer_ids": [7, 3, 999]},
            headers=MARKETER,
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

    # по умолчанию «всем в очереди» — только высокий и средний риск: 1, 4, 3 (низкий 5 — нет)
    queue = (
        await client.post(
            "/api/v1/mail/campaigns/preview", json={"worklist": True}, headers=MARKETER
        )
    ).json()
    assert queue["to_send"] == 3 and queue["templates"] == [
        {"code": "prolong_wait", "count": 3}
    ]
    counts = (await client.get("/api/v1/contact-today", headers=MARKETER)).json()
    assert counts["queue_by_level"] == {"high": 2, "medium": 1, "low": 1}
    everyone = {"worklist": True, "levels": ["high", "medium", "low"]}
    queue = (
        await client.post("/api/v1/mail/campaigns/preview", json=everyone, headers=MARKETER)
    ).json()
    assert queue["to_send"] == 4  # 1, 4, 3, 5 — уровни выбраны явно

    created = await client.post("/api/v1/mail/campaigns", json=everyone, headers=MARKETER)
    assert created.status_code == 202, created.text
    done = await _wait_done(client, created.json()["id"])
    assert (
        done["sent"] == 4
        and done["failed"] == 0
        and done["queued"] == 0
        and done["created_by"] == "marketer"
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
        "/api/v1/mail/campaigns", json={"customer_ids": [1, 3]}, headers=MARKETER
    )
    assert (
        again.status_code == 422 and again.json()["error"]["code"] == "nothing_to_send"
    )
    forced = await client.post(
        "/api/v1/mail/campaigns",
        json={"customer_ids": [1], "skip_days": 0},
        headers=MARKETER,
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


async def test_company_setting_and_template_preview_on_real_customer(env):
    """Превью шаблона — на настоящем клиенте и той же функцией, что отправка; подпись — из настроек."""
    client, _, _ = env
    settings_body = (await client.get("/api/v1/mail/settings", headers=ANALYST)).json()
    assert settings_body["company"] == "ChurnRadar"
    denied = await client.put("/api/v1/mail/settings", json={"company": "Хостинг Плюс"}, headers=ANALYST)
    assert denied.status_code == 403
    empty = await client.put("/api/v1/mail/settings", json={"company": "   "}, headers=ADMIN)
    assert empty.status_code == 422
    saved = await client.put("/api/v1/mail/settings", json={"company": " Хостинг Плюс "}, headers=ADMIN)
    assert saved.status_code == 200 and saved.json()["company"] == "Хостинг Плюс"

    draft = {"subject": "{{service}} ждёт оплаты", "body": "Баланс {{balance}}\n\nкоманда {{company}}"}
    preview = (
        await client.post("/api/v1/mail/preview/prolong_wait", json=draft, headers=ANALYST)
    ).json()
    # у клиента 1 наибольшая потеря и главная причина «ждёт оплаты» — ему этот шаблон и уйдёт
    assert preview["customer_id"] == 1 and preview["to"] == "anna@example.test"
    assert preview["applicable"] is True
    assert preview["subject"] == "service-1 ждёт оплаты"
    assert preview["text"] == "Баланс €5,00\n\nкоманда Хостинг Плюс"

    # черновик письма из карточки — те же подстановки
    letter = (await client.post("/api/v1/customers/1/email/draft", json={}, headers=ANALYST)).json()
    assert letter["text"].endswith("команда Хостинг Плюс")

    # причины «не хватит денег» нет ни у кого — пример не выдумываем
    nobody = (
        await client.post("/api/v1/mail/preview/insufficient_funds", json=draft, headers=ANALYST)
    ).json()
    assert nobody["customer_id"] is None and nobody["applicable"] is False

    chosen = (
        await client.post(
            "/api/v1/mail/preview/insufficient_funds", json={**draft, "customer_id": 3}, headers=ANALYST
        )
    ).json()
    assert chosen["customer_id"] == 3 and chosen["applicable"] is False
    bad = await client.post(
        "/api/v1/mail/preview/general", json={**draft, "body": "{{nope}}"}, headers=ANALYST
    )
    assert bad.status_code == 422


async def test_urgent_customer_goes_first_in_queue(env):
    """Срочно — срок не дальше двух дней при любом уровне: такой клиент поднимается наверх очереди."""
    client, _, _ = env
    services = '[{"id": 30, "name": "service-3", "status": "prolong_wait", "days_to_expiry": -4.0, "reasons": []}]'
    await _sql(
        "UPDATE scoring.customer_scores SET detail = jsonb_set(detail, '{services}', $1::jsonb) WHERE customer_id = 3",
        services,
    )
    queue = (await client.get("/api/v1/contact-today", headers=ANALYST)).json()
    first = queue["items"][0]
    assert first["id"] == 3 and first["risk_level"] == "medium"
    assert first["urgent"] is True and first["deadline_days"] == 1.0
    assert queue["urgent_total"] == 1
    assert all(item["urgent"] is False for item in queue["items"][1:])

    page = (await client.get("/api/v1/customers?level=medium", headers=ANALYST)).json()
    row = next(i for i in page["items"] if i["id"] == 3)
    assert row["urgent"] is True and row["next_action"]["code"] == "prolong_wait"
