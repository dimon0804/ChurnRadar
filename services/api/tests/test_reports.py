"""Еженедельный отчёт: числа, PDF и HTML, письмо с вложением, расписание; журнал действий api."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime, time, timedelta

import pytest
from pypdf import PdfReader

from app import audit_log, report_pdf, report_schedule, reports
from tests.conftest import ADMIN, ANALYST

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)

SUMMARY = {
    "customers_total": 1200,
    "by_level": {"high": 37, "medium": 120, "low": 900, "unknown": 143},
    "expected_loss": 1_234_567,
    "exposure": 5_000_000,
    "revenue_at_risk_share": 0.183,
    "horizon_days": 30,
    "scored_at": "2026-10-19T15:00:00Z",
    "model_version": 4,
    "top_reasons": [
        {"code": "low_balance", "title": "Баланс скоро кончится", "customers": 40, "expected_loss": 600_000},
        {"code": "no_autoprolong", "title": "Нет автопродления", "customers": 25, "expected_loss": 300_000},
    ],
    "timeline": [
        {"date": (date(2026, 10, 24) + timedelta(days=i)).isoformat(), "kind": "forecast",
         "expected_loss": 40_000 + i * 1_000}
        for i in range(30)
    ] + [{"date": "2026-10-01", "kind": "fact", "deletions": 3}],
}
MARKETING = {
    "mrr": 9_876_500, "arpu": 1_234, "ltv": 45_678, "ltv_at_risk": 777_700,
    "churn": {"customer_monthly": 0.042, "expected_mrr_loss": 333_300},
}
TOP = [{
    "customer_id": 17, "email": "ivan@example.com", "risk_score": 91.24, "risk_level": "high",
    "expected_loss": 55_500, "monthly_revenue": 9_900, "reasons": [{"title": "Баланс скоро кончится"}],
}]
DYNAMICS = [
    {"day": date(2026, 10, 1) + timedelta(days=i), "expected_loss": 1_000_000 + i * 10_000, "high_count": 30,
     "risk_share": 0.17}
    for i in range(23)
]
MAIL = {"sent": 12, "failed": 1, "customers": 10, "helped": 4, "helped_share": 0.4}
ACTS = {"campaigns": 2, "resolved": 5, "snoozed": 3}


@pytest.fixture
def report():
    return reports.build(SUMMARY, MARKETING, TOP, DYNAMICS, MAIL, ACTS, NOW)


def test_build_has_key_numbers_and_week_over_week_change(report):
    k = report["kpi"]
    assert (k["expected_loss"], k["high"], k["mrr"], k["arpu"], k["ltv"]) == (1_234_567, 37, 9_876_500, 1_234, 45_678)
    # неделю назад (17 октября) было 1 160 000
    assert k["expected_loss_change"] == pytest.approx(1_234_567 / 1_160_000 - 1)
    assert len(report["forecast"]) == 30 and len(report["history"]) == 23
    assert report["customers"][0]["reason"] == "Баланс скоро кончится"
    assert report["done"]["helped"] == 4 and report["period_from"] == "2026-10-17"


def test_money_format():
    assert reports.money(1_234_567) == "€12 346"
    assert reports.money(None) == "—"
    assert reports.pct(0.183) == "18,3%"


def test_pdf_is_generated_and_contains_key_numbers(report):
    pdf = report_pdf.render_pdf(report)
    assert pdf.startswith(b"%PDF-")
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    for value in ("€12 346", "€98 765", "€12", "€457", "ivan@example.com", "18,3%", "#17"):
        assert value in text, value
    if report_pdf.fonts()[0] == "Report":  # кириллический шрифт найден
        assert "Ожидаемая потеря" in text and "Баланс скоро кончится" in text


def test_pdf_without_history_and_customers_still_builds():
    empty = reports.build({"by_level": {}}, {}, [], [], {}, {}, NOW)
    assert report_pdf.render_pdf(empty).startswith(b"%PDF-")
    assert "Клиентов под риском нет" in reports.render_html(empty)


def test_html_report_and_email_variant(report):
    page = reports.render_html(report)
    assert "€12 346" in page and "<svg" in page and "ivan@example.com" in page
    mail = report_schedule.build_mail(report, b"%PDF-1.4", "boss@example.com")
    assert "<svg" not in mail.html and "€12 346" in mail.subject
    assert [(a.filename, a.content_type) for a in mail.attachments] == [
        ("churnradar-weekly-2026-10-24.pdf", "application/pdf")
    ]


def _row(**kw):
    base = {"enabled": True, "weekday": 6, "send_time": time(9, 0), "timezone": "Europe/Moscow",
            "recipients": ["boss@example.com"], "last_sent_on": None}
    return {**base, **kw}


def test_due_date_respects_weekday_time_zone_and_last_sent():
    # 24.10.2026 — суббота; 09:00 UTC = 12:00 в Москве
    assert report_schedule.due_date(_row(), NOW) == date(2026, 10, 24)
    assert report_schedule.due_date(_row(send_time=time(13, 0)), NOW) is None
    assert report_schedule.due_date(_row(weekday=1), NOW) is None
    assert report_schedule.due_date(_row(last_sent_on=date(2026, 10, 24)), NOW) is None
    assert report_schedule.due_date(_row(enabled=False), NOW) is None
    assert report_schedule.due_date(_row(recipients=[]), NOW) is None


async def test_weekly_report_endpoint(client, monkeypatch, report):
    async def fake_collect(pool, cache, now=None):
        return report

    monkeypatch.setattr(reports, "collect", fake_collect)
    assert (await client.get("/api/v1/reports/weekly")).status_code == 401
    r = await client.get("/api/v1/reports/weekly", headers=ANALYST)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "churnradar-weekly-2026-10-24.pdf" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF-")
    r = await client.get("/api/v1/reports/weekly?format=html", headers=ANALYST)
    assert r.status_code == 200 and "€12 346" in r.text


@pytest.mark.parametrize(
    "body",
    [
        {"enabled": True, "weekday": 1, "send_time": "09:00", "recipients": ["not-an-email"]},
        {"enabled": True, "weekday": 8, "send_time": "09:00", "recipients": []},
        {"enabled": True, "weekday": 1, "send_time": "25:00", "recipients": []},
        {"enabled": True, "weekday": 1, "send_time": "09:00", "timezone": "Mars/Base", "recipients": []},
    ],
)
async def test_schedule_validation(client, body):
    r = await client.put("/api/v1/reports/schedule", json=body, headers=ADMIN)
    assert r.status_code == 422


async def test_schedule_admin_only_and_forbidden_attempt_is_audited(client, app):
    body = {"enabled": False, "weekday": 1, "send_time": "09:00", "recipients": []}
    r = await client.put("/api/v1/reports/schedule", json=body, headers=ANALYST)
    assert r.status_code == 403
    (row,) = app.state.pool.audit
    assert row[0:3] == ("api", "analyst", "user") and row[4] == "report.schedule.update" and row[7] is False
    assert (await client.get("/api/v1/audit", headers=ANALYST)).status_code == 403
    assert (await client.get("/api/v1/audit/export.csv", headers=ANALYST)).status_code == 403


async def test_send_now_requires_recipients_and_mail(client, app):
    app.state.mail_config = report_schedule.MailConfig(host="")
    r = await client.post("/api/v1/reports/weekly/send", json={"recipients": []}, headers=ADMIN)
    assert r.status_code == 422
    r = await client.post("/api/v1/reports/weekly/send", json={"recipients": ["a@example.com"]}, headers=ADMIN)
    assert r.status_code == 503 and r.json()["error"]["code"] == "mail_disabled"


async def test_audit_list_and_export_for_admin(client, app):
    from tests.conftest import FakePool

    row = {
        "id": 1, "at": NOW, "service": "auth", "actor": "admin", "actor_type": "user", "actor_role": "admin",
        "action": "token.create", "object_type": "token", "object_id": "aeza", "success": True,
        "ip": "10.0.0.1", "request_id": "rid", "details": '{"role": "analyst"}',
    }
    app.state.pool = FakePool({"count(*)": 1, "DISTINCT action": [{"action": "token.create"}], "audit.events": [row]})
    r = await client.get("/api/v1/audit?action=token&date_from=2026-10-01&success=true", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1 and body["items"][0]["action_title"] == "Создан API-токен"
    assert body["items"][0]["details"] == {"role": "analyst"} and body["items"][0]["at"] == "2026-10-24T09:00:00.000Z"
    r = await client.get("/api/v1/audit/export.csv", headers=ADMIN)
    assert r.status_code == 200 and "Создан API-токен" in r.content.decode("utf-8-sig")
    assert (await client.get("/api/v1/audit?actor_type=robot", headers=ADMIN)).status_code == 422


def test_audit_filters_sql():
    sql, args = audit_log.where(
        q="aeza", action="token", actor_type="token", success=False,
        date_from=date(2026, 10, 1), date_to=date(2026, 10, 2),
    )
    assert sql.startswith(" WHERE ") and "action = $2 OR action LIKE $3" in sql
    assert args[0] == "%aeza%" and args[1:4] == ["token", "token.%", "token"]
    assert args[-1] == datetime(2026, 10, 3, tzinfo=UTC)
    assert audit_log.where() == ("", [])


def test_every_api_change_route_has_an_audit_rule(app):
    """Новый изменяющий эндпоинт без правила попадёт в журнал как http.*, но лучше назвать его явно."""
    import re

    unsafe = {
        (m, r.path) for r in app.routes for m in getattr(r, "methods", ()) or ()
        if m in {"POST", "PUT", "PATCH", "DELETE"}
    }
    for method, path in unsafe:
        sample = re.sub(r"\{[^}]+\}", "1", path)
        assert any(rule.method == method and rule.pattern.match(sample) for rule in audit_log.API_RULES), (method, path)
