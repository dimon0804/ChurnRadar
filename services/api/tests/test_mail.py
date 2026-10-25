"""Unit-тесты писем клиентам: шаблоны, подстановки, выбор шаблона, проверки запросов."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from svckit.errors import ApiError
from svckit.mail import MailConfig

from app import mail_templates as mt
from app import mailer
from tests.conftest import ADMIN, ANALYST, MARKETER

REF = datetime(2026, 10, 19, 15, tzinfo=UTC)

CUSTOMER = {
    "customer_id": 9,
    "email": "user-0009@example.test",
    "risk_level": "high",
    "risk_score": 72.4,
    "expected_loss": 138497,
    "balance": 91506,
    "reasons": [
        {"code": "insufficient_funds", "service_id": 19},
        {"code": "suspended", "service_id": 19},
    ],
    "detail": {
        "services": [
            {
                "id": 9,
                "name": "service-0009",
                "price": 850,
                "expires_at": "2026-09-09T15:00:00Z",
                "expected_loss": 711,
            },
            {
                "id": 19,
                "name": "service-0019",
                "price": 855,
                "payment_term": "hour",
                "expires_at": "2026-11-18T15:00:00Z",
                "expected_loss": 60000,
            },
        ],
        "money": {"funds_run_out_at": "2026-10-24T02:00:00Z"},
    },
}


def test_default_templates_use_only_known_placeholders():
    codes = [t.code for t in mt.DEFAULTS]
    assert len(codes) == len(set(codes))
    assert {
        "insufficient_funds",
        "autoprolong_off_expiring",
        "prolong_wait",
        "suspended",
        "failed_payments",
        "inactivity",
        "general",
    } <= set(codes)
    for t in mt.DEFAULTS:
        assert mt.unknown_placeholders(t.subject + t.body) == [], t.code
        text = mt.render(t.body, mt.context(CUSTOMER, t.code, REF, "ChurnRadar"))
        assert "{{" not in text and "}}" not in text
        assert "звон" not in (t.subject + t.body).lower()


def test_validate_placeholders():
    mt.validate(mt.TemplatePatch(subject="Привет, {{ email }}", body="До {{date}}"))
    with pytest.raises(ApiError) as err:
        mt.validate(mt.TemplatePatch(subject="{{name}}", body="{{date}} {{foo}}"))
    assert err.value.status == 422
    assert [d["field"] for d in err.value.details] == ["subject", "body"]
    assert "{{foo}}" in err.value.details[1]["message"]
    with pytest.raises(ApiError):
        mt.validate(mt.TemplatePatch(subject="  ", body="x"))
    with pytest.raises(ValidationError):
        mt.TemplatePatch(subject="", body="x")


def test_render_falls_back_when_value_missing():
    assert (
        mt.render("до {{date}}, {{ service }}", {"date": "", "service": "s-1"})
        == "до конца оплаченного срока, s-1"
    )


def test_money_and_date_text():
    assert mt.money_text(91506) == "€915,06"
    assert mt.money_text(616455) == "€6 164,55"
    assert mt.money_text(None) == ""
    assert mt.price_text({"price": 850, "payment_term": "month"}) == "€8,50 в месяц"
    assert mt.price_text({"price": 850}) == "€8,50"
    assert mt.price_text(None) == ""
    assert mt.date_text(datetime(2026, 10, 24, 2, tzinfo=UTC)) == "24 октября"


def test_choose_template():
    codes = {t.code for t in mt.DEFAULTS}
    assert (
        mt.choose_template(CUSTOMER["reasons"], "high", codes) == "insufficient_funds"
    )
    assert (
        mt.choose_template([{"code": "short_commitment"}], "medium", codes) == "general"
    )
    assert mt.choose_template([], "unknown", codes) == "win_back"
    assert mt.choose_template([], "low", codes) == "general"


def test_context_values():
    values = mt.context(CUSTOMER, "insufficient_funds", REF, "Хостинг")
    assert values == {
        "email": "user-0009@example.test",
        "service": "service-0019",
        "date": "24 октября",  # когда кончатся деньги
        "amount": "€8,55 в час",
        "balance": "€915,06",
        "company": "Хостинг",
    }
    # для другой причины — дата окончания услуги
    assert mt.context(CUSTOMER, "suspended", REF, "x")["date"] == "18 ноября"
    # дата из прошлого не подставляется: срок уже наступил — значит «сегодня»
    late = datetime(2026, 12, 1, tzinfo=UTC)
    assert mt.context(CUSTOMER, "suspended", late, "x")["date"] == "1 декабря"
    # пустое название компании — подпись по умолчанию
    assert mt.context(CUSTOMER, "suspended", REF, "  ")["company"] == "ChurnRadar"


def test_balance_in_letter_matches_card_with_bonuses():
    """Жюри: баланс в двух местах не совпадал (€49,47 и €43,32) — одно было с бонусами."""
    customer = {**CUSTOMER, "balance": 4_332, "bonus_balance": 615, "bonus_state": "unlocked"}
    assert mt.context(customer, "insufficient_funds", REF, "x")["balance"] == "€49,47 (вкл. бонусы €6,15)"
    locked = {**customer, "bonus_state": "locked"}
    assert mt.context(locked, "insufficient_funds", REF, "x")["balance"] == "€43,32"


def test_template_applies_only_to_real_reason():
    """Совет «пополните баланс до …» клиенту, у которого денег хватает, не предлагаем."""
    assert mt.applicable("insufficient_funds", CUSTOMER)
    rich = {**CUSTOMER, "reasons": [{"code": "suspended", "service_id": 19}], "detail": {"services": []}}
    assert not mt.applicable("insufficient_funds", rich)
    assert mt.applicable("suspended", rich)
    assert mt.applicable("general", rich)
    assert not mt.applicable("win_back", rich)
    assert mt.applicable("win_back", {**rich, "risk_level": "unknown"})
    # причина может быть только у услуги, не в топе клиента
    nested = {
        **rich,
        "detail": {"services": [{"id": 19, "reasons": [{"code": "failed_payments"}]}]},
    }
    assert mt.applicable("failed_payments", nested)


def test_rendered_letter_reads_well():
    tpl = mt.BY_CODE["insufficient_funds"]
    values = mt.context(CUSTOMER, tpl.code, REF, "ChurnRadar")
    subject = mt.render(tpl.subject, values)
    body = mt.render(tpl.body, values)
    assert (
        subject
        == "Пополните баланс до 24 октября, чтобы service-0019 работала без перерыва"
    )
    assert "На вашем балансе сейчас €915,06" in body
    assert body.endswith("команда ChurnRadar")


def test_to_html_escapes():
    assert mt.to_html("a <b>\nc\n\nd") == "<p>a &lt;b&gt;<br>c</p><p>d</p>"


def test_campaign_request():
    mailer.CampaignRequest(worklist=True)
    mailer.CampaignRequest(customer_ids=[1, 2], skip_days=0)
    for bad in (
        {},
        {"worklist": True, "customer_ids": [1]},
        {"customer_ids": []},
        {"worklist": True, "skip_days": 91},
    ):
        with pytest.raises(ValidationError):
            mailer.CampaignRequest.model_validate(bad)


def test_reply_to():
    cfg = MailConfig(host="x", from_addr="care@example.test")
    assert (
        mailer.reply_to_for("anna@corp.test", "team@corp.test", cfg) == "anna@corp.test"
    )
    assert mailer.reply_to_for(None, "team@corp.test", cfg) == "team@corp.test"
    assert mailer.reply_to_for(" ", "", cfg) == "care@example.test"


# --- эндпоинты ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/mail/templates", None),
        ("PUT", "/api/v1/mail/templates/general", {"subject": "s", "body": "b"}),
        ("POST", "/api/v1/customers/1/email/draft", {}),
        ("POST", "/api/v1/customers/1/email", {"subject": "s", "text": "t"}),
        ("POST", "/api/v1/mail/campaigns/preview", {"worklist": True}),
        ("POST", "/api/v1/mail/campaigns", {"worklist": True}),
        ("GET", "/api/v1/mail/campaigns", None),
        ("GET", "/api/v1/mail/stats", None),
    ],
)
async def test_mail_requires_user(client, method, path, body):
    resp = await client.request(method, path, json=body)
    assert resp.status_code == 401


async def test_templates_admin_only(client):
    for method, path, body in (
        ("PUT", "/api/v1/mail/templates/general", {"subject": "s", "body": "b"}),
        ("POST", "/api/v1/mail/templates/general/reset", None),
    ):
        resp = await client.request(method, path, json=body, headers=ANALYST)
        assert resp.status_code == 403 and resp.json()["error"]["code"] == "forbidden"


async def test_send_without_smtp_is_503(client, app):
    app.state.mail_config = MailConfig()
    resp = await client.post(
        "/api/v1/customers/1/email", json={"subject": "s", "text": "t"}, headers=MARKETER
    )
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "mail_disabled"
    resp = await client.post(
        "/api/v1/mail/campaigns", json={"worklist": True}, headers=ADMIN
    )
    assert resp.status_code == 503


async def test_unknown_template_is_422(client, app):
    app.state.mail_config = MailConfig(host="x")
    resp = await client.post(
        "/api/v1/customers/1/email",
        json={"template_code": "nope", "subject": "s", "text": "t"},
        headers=MARKETER,
    )
    assert resp.status_code == 422



def test_mass_campaign_defaults_to_high_and_medium():
    """Жюри: «Написать всем · 84» уходило почти всей базе. По умолчанию — только высокий и средний риск."""
    req = mailer.CampaignRequest(worklist=True)
    assert req.queue_levels == ["high", "medium"]
    assert mailer.CampaignRequest(worklist=True, levels=["low", "low"]).queue_levels == ["low"]
    with pytest.raises(ValidationError):
        mailer.CampaignRequest(worklist=True, levels=[])
    with pytest.raises(ValidationError):
        mailer.CampaignRequest(worklist=True, levels=["unknown"])
    with pytest.raises(ValidationError):
        mailer.CampaignRequest(customer_ids=[1], levels=["high"])


def test_forced_template_falls_back_to_real_reason_in_campaign():
    templates = {t.code: {"subject": t.subject, "body": t.body} for t in mt.DEFAULTS}
    rich = {**CUSTOMER, "reasons": [{"code": "suspended", "service_id": 19}]}
    rich["detail"] = {**CUSTOMER["detail"], "services": [{**s, "reasons": []} for s in CUSTOMER["detail"]["services"]]}
    code, subject, _ = mailer._compose(rich, templates, "insufficient_funds", REF, "X", strict=True)
    assert code == "suspended" and "Пополните" not in subject
    # из карточки сотрудник выбирает шаблон сам и видит текст до отправки
    code, _, _ = mailer._compose(rich, templates, "insufficient_funds", REF, "X")
    assert code == "insufficient_funds"
