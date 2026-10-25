"""Шаблоны писем клиентам: тексты по умолчанию, подстановки, выбор по причине риска."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, Field
from svckit.errors import ApiError

from app.queries import iso_z

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

PLACEHOLDERS: dict[str, str] = {
    "email": "Email клиента",
    "service": "Услуга, к которой относится причина (например, service-0019)",
    "date": "Ключевая дата: когда кончатся деньги на балансе или оплаченный срок услуги",
    "amount": "Цена услуги за период оплаты, например «€8,50 в месяц»",
    "balance": "Баланс клиента, как в карточке: с бонусами, если они разблокированы",
    "company": "Название компании из настроек писем (по умолчанию ChurnRadar)",
}
DEFAULT_COMPANY = "ChurnRadar"

# подстановка, когда данных нет: тексты написаны так, чтобы фраза оставалась связной
FALLBACK: dict[str, str] = {
    "email": "",
    "service": "вашей услуги",
    "date": "конца оплаченного срока",
    "amount": "столько, сколько указано в тарифе",
    "balance": "немного",
    "company": "ChurnRadar",
}

SIGNATURE = "\n\nС уважением,\nкоманда {{company}}"


@dataclass(frozen=True, slots=True)
class TemplateDefault:
    code: str
    title: str
    subject: str
    body: str


DEFAULTS: tuple[TemplateDefault, ...] = (
    TemplateDefault(
        "insufficient_funds",
        "Не хватит денег на продление",
        "Пополните баланс до {{date}}, чтобы {{service}} работала без перерыва",
        "Здравствуйте!\n\n"
        "На вашем балансе сейчас {{balance}}, а {{service}} стоит {{amount}}. "
        "Пополните, пожалуйста, баланс до {{date}} — тогда услуга продлится сама и сервер не остановится.\n\n"
        "Если нужен другой тариф или удобнее платить иначе, просто ответьте на это письмо, подберём вариант."
        + SIGNATURE,
    ),
    TemplateDefault(
        "autoprolong_off_expiring",
        "Автопродление выключено",
        "{{service}}: включите автопродление",
        "Здравствуйте!\n\n"
        "У {{service}} выключено автопродление, а оплаченный срок действует до {{date}}. "
        "Чтобы сервер не остановился, включите автопродление в личном кабинете или продлите услугу вручную. "
        "Услуга стоит {{amount}}.\n\n"
        "Если услуга больше не нужна, ответьте на письмо — подскажем, как сохранить данные."
        + SIGNATURE,
    ),
    TemplateDefault(
        "prolong_wait",
        "Ждёт оплаты",
        "{{service}} ждёт оплаты",
        "Здравствуйте!\n\n"
        "Срок оплаты {{service}} уже наступил, и услуга ждёт продления. Если её не продлить, сервер "
        "и данные на нём могут удалить. Продлить можно в личном кабинете, услуга стоит {{amount}}.\n\n"
        "Если с оплатой что-то не получается, ответьте на это письмо — разберёмся вместе."
        + SIGNATURE,
    ),
    TemplateDefault(
        "suspended",
        "Сервер выключен",
        "Всё ли в порядке с {{service}}?",
        "Здравствуйте!\n\n"
        "Мы заметили, что {{service}} выключен, хотя услуга оплачивается. Если сервер сейчас не нужен "
        "или не хватает мощности, напишите нам: подберём подходящий тариф или поможем с настройкой."
        + SIGNATURE,
    ),
    TemplateDefault(
        "failed_payments",
        "Платежи не проходят",
        "Не проходит оплата за {{service}}",
        "Здравствуйте!\n\n"
        "Последние платежи за {{service}} не прошли. Чаще всего у карты закончился срок действия "
        "или на ней не хватило денег. Проверьте, пожалуйста, способ оплаты в личном кабинете, "
        "чтобы услуга продлилась вовремя.\n\n"
        "Если нужна помощь, просто ответьте на это письмо." + SIGNATURE,
    ),
    TemplateDefault(
        "inactivity",
        "Давно не платил",
        "Как вам у нас?",
        "Здравствуйте!\n\n"
        "Давно не видели от вас пополнений и хотим убедиться, что всё в порядке. Если что-то "
        "не устраивает — цена, скорость, поддержка, — ответьте на это письмо. Прочитаем и постараемся помочь."
        + SIGNATURE,
    ),
    TemplateDefault(
        "win_back",
        "Клиент ушёл",
        "Будем рады видеть вас снова",
        "Здравствуйте!\n\n"
        "Ваши услуги у нас сейчас закрыты. Если захотите вернуться, ответьте на это письмо: "
        "подберём тариф и поможем перенести проекты." + SIGNATURE,
    ),
    TemplateDefault(
        "general",
        "Общий",
        "Нужна помощь с {{service}}?",
        "Здравствуйте!\n\n"
        "Хотим убедиться, что с {{service}} всё в порядке. Если есть вопросы по оплате, тарифу "
        "или настройке, просто ответьте на это письмо — поможем." + SIGNATURE,
    ),
)
BY_CODE = {t.code: t for t in DEFAULTS}
GENERAL = "general"
WIN_BACK = "win_back"

MONTHS_GEN = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


class MailSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str = Field(min_length=1, max_length=100)


class TemplatePreview(BaseModel):
    """Черновик шаблона из редактора: показываем его на настоящем клиенте."""

    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)
    customer_id: int | None = Field(default=None, ge=1)


class TemplatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)


def unknown_placeholders(text: str) -> list[str]:
    return sorted(
        {m.group(1) for m in PLACEHOLDER_RE.finditer(text)} - set(PLACEHOLDERS)
    )


def validate(patch: TemplatePatch) -> None:
    errors = []
    for field in ("subject", "body"):
        value = getattr(patch, field)
        if not value.strip():
            errors.append(
                {"field": field, "message": "Не может быть пустым", "type": "empty"}
            )
        bad = unknown_placeholders(value)
        if bad:
            names = ", ".join("{{" + b + "}}" for b in bad)
            allowed = ", ".join("{{" + p + "}}" for p in PLACEHOLDERS)
            errors.append(
                {
                    "field": field,
                    "message": f"Неизвестные подстановки: {names}. Можно: {allowed}",
                    "type": "placeholder",
                }
            )
    if errors:
        raise ApiError(422, "validation_error", "Шаблон не прошёл проверку", errors)


def render(text: str, values: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        value = values.get(name) or FALLBACK.get(name, "")
        return value

    return PLACEHOLDER_RE.sub(sub, text)


def to_html(text: str) -> str:
    """Простой HTML из текста письма: абзацы и переносы строк."""
    paras = [p for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras)


def money_text(cents: int | None) -> str:
    if cents is None:
        return ""
    whole, rest = divmod(int(cents), 100)
    return f"€{whole:,}".replace(",", " ") + f",{rest:02d}"


TERM_TEXT = {
    "hour": "в час",
    "week": "в неделю",
    "month": "в месяц",
    "quarter_year": "за квартал",
    "half_year": "за полгода",
    "year": "в год",
}


def funds_text(customer: dict[str, Any]) -> str:
    """Баланс так же, как в карточке и в объяснениях модели: «€49,47 (вкл. бонусы €6,15)»."""
    balance = customer.get("balance")
    if balance is None:
        return ""
    bonus = int(customer.get("bonus_balance") or 0) if customer.get("bonus_state") == "unlocked" else 0
    if bonus > 0:
        return f"{money_text(int(balance) + bonus)} (вкл. бонусы {money_text(bonus)})"
    return money_text(balance)


def price_text(service: dict[str, Any] | None) -> str:
    """«€8,55 в час»: цена за период оплаты, без периода сумма вводит в заблуждение."""
    if not service or not service.get("price"):
        return ""
    term = TERM_TEXT.get(service.get("payment_term") or "")
    return (
        f"{money_text(service['price'])} {term}"
        if term
        else money_text(service["price"])
    )


def date_text(value: datetime | None) -> str:
    if value is None:
        return ""
    value = value.astimezone(UTC)
    return f"{value.day} {MONTHS_GEN[value.month - 1]}"


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def reason_codes(customer: dict[str, Any]) -> set[str]:
    """Все причины риска клиента: главные и по каждой услуге."""
    codes = {r.get("code") for r in customer.get("reasons") or [] if isinstance(r, dict)}
    for service in (customer.get("detail") or {}).get("services") or []:
        if isinstance(service, dict):
            codes.update(r.get("code") for r in service.get("reasons") or [] if isinstance(r, dict))
    codes.discard(None)
    return codes  # type: ignore[return-value]


def applicable(code: str, customer: dict[str, Any]) -> bool:
    """Подходит ли шаблон клиенту по его настоящей причине.

    Шаблон причины («Пополните баланс до …», «включите автопродление») утверждает факт о
    клиенте. Если у клиента такой причины нет — например, денег на продление хватает, —
    письмо по нему вводило бы в заблуждение. Общий шаблон подходит всем, «вернуться» — ушедшим.
    """
    if code == GENERAL:
        return True
    if code == WIN_BACK:
        return customer.get("risk_level") == "unknown"
    return code in reason_codes(customer)


def choose_template(
    reasons: list[dict[str, Any]], risk_level: str, codes: set[str]
) -> str:
    """Шаблон по главной причине; у ушедших клиентов — «вернуться», иначе общий."""
    if risk_level == "unknown":
        return WIN_BACK if WIN_BACK in codes else GENERAL
    main = reasons[0].get("code") if reasons else None
    return main if main in codes else GENERAL


def context(
    customer: dict[str, Any], template_code: str, now: datetime, company: str
) -> dict[str, str]:
    """Значения подстановок по оценке клиента — одна функция для предпросмотра и отправки.

    Дата не раньше сегодняшней по опорному времени данных: срок, который уже наступил,
    становится «сегодня», а не датой из прошлого.
    """
    detail = customer.get("detail") or {}
    services = {
        s.get("id"): s for s in detail.get("services") or [] if isinstance(s, dict)
    }
    reasons = customer.get("reasons") or []
    reason = next(
        (r for r in reasons if r.get("code") == template_code),
        reasons[0] if reasons else {},
    )
    service = services.get(reason.get("service_id")) if reason else None
    if service is None and services:
        service = max(services.values(), key=lambda s: s.get("expected_loss") or 0)
    money = detail.get("money") or {}

    date: datetime | None = None
    if template_code == "insufficient_funds":
        date = _parse(money.get("funds_run_out_at"))
    if date is None and service is not None:
        date = _parse(service.get("expires_at"))
    if date is not None and date <= now:
        date = now
    return {
        "email": customer.get("email") or "",
        "service": (service or {}).get("name") or "",
        "date": date_text(date),
        "amount": price_text(service),
        "balance": funds_text(customer),
        "company": company.strip() or DEFAULT_COMPANY,
    }


# --- хранение ----------------------------------------------------------------------------

_SETTINGS_SQL = "SELECT company, updated_at, updated_by FROM scoring.mail_settings WHERE id = 1"


def settings_out(row: asyncpg.Record | dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {"company": DEFAULT_COMPANY, "updated_at": None, "updated_by": None}
    return {
        "company": row["company"] or DEFAULT_COMPANY,
        "updated_at": iso_z(row["updated_at"]),
        "updated_by": row["updated_by"],
    }


async def load_settings(conn: asyncpg.Connection) -> dict[str, Any]:
    return settings_out(await conn.fetchrow(_SETTINGS_SQL))


async def company(conn: asyncpg.Connection) -> str:
    """Название компании для подписи писем ({{company}})."""
    return str((await load_settings(conn))["company"])


async def get_settings(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return await load_settings(conn)


async def put_settings(pool: asyncpg.Pool, update: MailSettingsUpdate, user: str | None) -> dict[str, Any]:
    name = update.company.strip()
    if not name:
        raise ApiError(
            422,
            "validation_error",
            "Название компании не может быть пустым",
            [{"field": "company", "message": "Не может быть пустым", "type": "empty"}],
        )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO scoring.mail_settings (id, company, updated_at, updated_by) VALUES (1, $1, now(), $2) "
            "ON CONFLICT (id) DO UPDATE SET company = EXCLUDED.company, updated_at = EXCLUDED.updated_at, "
            "updated_by = EXCLUDED.updated_by RETURNING company, updated_at, updated_by",
            name,
            user,
        )
    return settings_out(row)



async def ensure_defaults(conn: asyncpg.Connection) -> None:
    await conn.executemany(
        "INSERT INTO scoring.mail_templates (code, subject, body, updated_by) VALUES ($1, $2, $3, NULL) "
        "ON CONFLICT (code) DO NOTHING",
        [(t.code, t.subject, t.body) for t in DEFAULTS],
    )


def template_out(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
    default = BY_CODE.get(row["code"])
    return {
        "code": row["code"],
        "title": default.title if default else row["code"],
        "subject": row["subject"],
        "body": row["body"],
        "is_default": bool(
            default
            and default.subject == row["subject"]
            and default.body == row["body"]
        ),
        "updated_at": iso_z(row["updated_at"]),
        "updated_by": row["updated_by"],
    }


async def load_all(conn: asyncpg.Connection) -> dict[str, dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT code, subject, body, updated_at, updated_by FROM scoring.mail_templates WHERE code = ANY($1::text[])",
        [t.code for t in DEFAULTS],
    )
    if len(rows) < len(DEFAULTS):
        await ensure_defaults(conn)
        rows = await conn.fetch(
            "SELECT code, subject, body, updated_at, updated_by FROM scoring.mail_templates "
            "WHERE code = ANY($1::text[])",
            [t.code for t in DEFAULTS],
        )
    order = {t.code: i for i, t in enumerate(DEFAULTS)}
    return {
        r["code"]: template_out(r) for r in sorted(rows, key=lambda r: order[r["code"]])
    }


async def list_templates(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        templates = await load_all(conn)
    return {
        "items": list(templates.values()),
        "placeholders": [
            {"name": k, "description": v} for k, v in PLACEHOLDERS.items()
        ],
    }


def _known(code: str) -> TemplateDefault:
    default = BY_CODE.get(code)
    if default is None:
        raise ApiError(404, "not_found", f"Шаблона «{code}» нет")
    return default


async def save_template(
    pool: asyncpg.Pool, code: str, patch: TemplatePatch, user: str | None
) -> dict[str, Any]:
    _known(code)
    validate(patch)
    async with pool.acquire() as conn:
        await ensure_defaults(conn)
        row = await conn.fetchrow(
            "UPDATE scoring.mail_templates SET subject = $2, body = $3, updated_at = now(), updated_by = $4 "
            "WHERE code = $1 RETURNING code, subject, body, updated_at, updated_by",
            code,
            patch.subject.strip(),
            patch.body.strip(),
            user,
        )
    return template_out(row)


async def reset_template(
    pool: asyncpg.Pool, code: str, user: str | None
) -> dict[str, Any]:
    default = _known(code)
    return await save_template(
        pool, code, TemplatePatch(subject=default.subject, body=default.body), user
    )
