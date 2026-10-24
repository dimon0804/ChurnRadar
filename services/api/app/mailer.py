"""Письма клиентам: одно письмо из карточки, рассылка по очереди, фоновая отправка, эффективность.

Одно письмо уходит прямо в запросе: сотрудник ждёт ответа «отправлено». Рассылка — нет:
запрос только кладёт письма в scoring.customer_emails со статусом queued, а отправляет их
MailWorker в фоне с ограничением скорости. Строки забираются FOR UPDATE SKIP LOCKED, поэтому
реплик api может быть несколько, и одно письмо не уйдёт дважды.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime, timedelta
from typing import Any

import asyncpg
import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator
from svckit.errors import ApiError
from svckit.mail import Mail, MailConfig, send_mail

from app import actions, mail_templates
from app.queries import BIGINT_MAX, iso_z

log = structlog.get_logger()

MAX_CAMPAIGN = 5000
STALE_SENDING = timedelta(minutes=10)

_CUSTOMER_SQL = (
    "SELECT customer_id, email, risk_level, risk_score, expected_loss, balance, reasons, detail "
    "FROM scoring.customer_scores WHERE customer_id = ANY($1::bigint[])"
)
_NOW_SQL = "SELECT now() AS now, COALESCE((SELECT reference_time FROM source.clock WHERE id = 1), now()) AS ref"


class EmailDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_code: str | None = Field(default=None, max_length=64)


class EmailSend(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_code: str | None = Field(default=None, max_length=64)
    subject: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=10_000)


class CampaignRequest(BaseModel):
    """Кому писать: выбранным (customer_ids) или всей очереди (worklist). Ровно что-то одно."""

    model_config = ConfigDict(extra="forbid")
    customer_ids: list[int] | None = Field(default=None, min_length=1, max_length=MAX_CAMPAIGN)
    worklist: bool = False
    template_code: str | None = Field(default=None, max_length=64)
    skip_days: int = Field(default=7, ge=0, le=90)

    @model_validator(mode="after")
    def _who(self) -> CampaignRequest:
        if bool(self.customer_ids) == self.worklist:
            raise ValueError("Укажите customer_ids или worklist=true (что-то одно)")
        return self


def reply_to_for(user_email: str | None, configured: str, config: MailConfig) -> str:
    """Ответ клиента должен прийти сотруднику; если его почты не знаем — общий адрес."""
    return (user_email or "").strip() or configured.strip() or config.from_addr


def _check_template(code: str | None) -> None:
    if code is not None and code not in mail_templates.BY_CODE:
        raise ApiError(
            422,
            "validation_error",
            f"Шаблона «{code}» нет",
            [{"field": "template_code", "message": "Неизвестный шаблон", "type": "enum"}],
        )


def _check_enabled(config: MailConfig) -> None:
    if not config.enabled:
        raise ApiError(503, "mail_disabled", "Отправка писем не настроена: задайте SMTP_HOST")


async def _customers(conn: asyncpg.Connection, ids: list[int]) -> dict[int, dict[str, Any]]:
    rows = await conn.fetch(_CUSTOMER_SQL, ids)
    return {r["customer_id"]: dict(r) for r in rows}


def _compose(
    customer: dict[str, Any],
    templates: dict[str, dict[str, Any]],
    code: str | None,
    ref: datetime,
    company: str,
) -> tuple[str, str, str]:
    """(код шаблона, тема, текст) для клиента."""
    if code is None:
        code = mail_templates.choose_template(customer.get("reasons") or [], customer["risk_level"], set(templates))
    values = mail_templates.context(customer, code, ref, company)
    tpl = templates[code]
    return code, mail_templates.render(tpl["subject"], values), mail_templates.render(tpl["body"], values)


async def _last_sent(conn: asyncpg.Connection, customer_id: int) -> datetime | None:
    return await conn.fetchval(
        "SELECT max(sent_at) FROM scoring.customer_emails WHERE customer_id = $1 AND status = 'sent'",
        customer_id,
    )


async def draft(
    pool: asyncpg.Pool, customer_id: int, body: EmailDraft, config: MailConfig, reply_to: str
) -> dict[str, Any]:
    actions.check_id(customer_id)
    _check_template(body.template_code)
    async with pool.acquire() as conn:
        clock = await conn.fetchrow(_NOW_SQL)
        customer = (await _customers(conn, [customer_id])).get(customer_id)
        if customer is None:
            raise ApiError(404, "not_found", f"Клиент {customer_id} не найден или ещё не оценён")
        templates = await mail_templates.load_all(conn)
        last = await _last_sent(conn, customer_id)
    code, subject, text = _compose(customer, templates, body.template_code, clock["ref"], config.from_name)
    return {
        "customer_id": customer_id,
        "to": customer["email"],
        "reply_to": reply_to,
        "from": config.from_addr,
        "template_code": code,
        "subject": subject,
        "text": text,
        "last_sent_at": iso_z(last),
        "enabled": config.enabled,
    }


async def _snapshot_insert(
    conn: asyncpg.Connection,
    customer: dict[str, Any],
    *,
    campaign_id: int | None,
    reply_to: str,
    code: str | None,
    subject: str,
    text: str,
    status: str,
    user: str | None,
    skip_reason: str | None = None,
) -> int:
    return await conn.fetchval(
        """
        INSERT INTO scoring.customer_emails (campaign_id, customer_id, to_email, reply_to, template_code,
            subject, body, status, skip_reason, risk_score_at_send, expected_loss_at_send, balance_at_send,
            created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13) RETURNING id
        """,
        campaign_id,
        customer["customer_id"],
        customer["email"],
        reply_to,
        code,
        subject,
        text,
        status,
        skip_reason,
        customer["risk_score"],
        customer["expected_loss"],
        customer["balance"],
        user,
    )


def _mail(row: dict[str, Any] | asyncpg.Record) -> Mail:
    return Mail(
        to=row["to_email"],
        subject=row["subject"],
        text=row["body"],
        html=mail_templates.to_html(row["body"]),
        reply_to=row["reply_to"],
        headers={"X-ChurnRadar-Customer": str(row["customer_id"])},
    )


async def _mark_sent(
    conn: asyncpg.Connection, email_id: int, message_id: str, customer_id: int, subject: str, user: str | None,
    followup_days: int,
) -> None:
    async with conn.transaction():
        await conn.execute(
            "UPDATE scoring.customer_emails SET status = 'sent', message_id = $2, sent_at = now(), error = NULL "
            "WHERE id = $1",
            email_id,
            message_id,
        )
        until = await conn.fetchval("SELECT now() + make_interval(days => $1)", followup_days)
        await actions.record_action(conn, customer_id, "emailed", until, subject, user, email_id)


async def _mark_failed(conn: asyncpg.Connection, email_id: int, error: str) -> None:
    await conn.execute(
        "UPDATE scoring.customer_emails SET status = 'failed', error = $2 WHERE id = $1", email_id, error[:500]
    )


def email_out(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "customer_id": row["customer_id"],
        "campaign_id": row["campaign_id"],
        "to": row["to_email"],
        "reply_to": row["reply_to"],
        "template_code": row["template_code"],
        "subject": row["subject"],
        "status": row["status"],
        "skip_reason": row["skip_reason"],
        "error": row["error"],
        "created_at": iso_z(row["created_at"]),
        "created_by": row["created_by"],
        "sent_at": iso_z(row["sent_at"]),
    }


async def send_one(
    pool: asyncpg.Pool,
    customer_id: int,
    body: EmailSend,
    config: MailConfig,
    reply_to: str,
    user: str | None,
    followup_days: int,
) -> dict[str, Any]:
    actions.check_id(customer_id)
    _check_template(body.template_code)
    _check_enabled(config)
    async with pool.acquire() as conn:
        clock = await conn.fetchrow(_NOW_SQL)
        customer = (await _customers(conn, [customer_id])).get(customer_id)
        if customer is None:
            raise ApiError(404, "not_found", f"Клиент {customer_id} не найден или ещё не оценён")
        if not customer["email"]:
            raise ApiError(422, "validation_error", "У клиента нет email")
        # сотрудник мог оставить подстановки в тексте — дозаполняем их
        code = body.template_code
        values = mail_templates.context(customer, code or mail_templates.GENERAL, clock["ref"], config.from_name)
        subject = mail_templates.render(body.subject.strip(), values)
        text = mail_templates.render(body.text.strip(), values)
        email_id = await _snapshot_insert(
            conn, customer, campaign_id=None, reply_to=reply_to, code=code, subject=subject, text=text,
            status="sending", user=user,
        )
        row = {"to_email": customer["email"], "subject": subject, "body": text, "reply_to": reply_to,
               "customer_id": customer_id}
        try:
            message_id = await send_mail(config, _mail(row))
        except Exception as exc:  # SMTP-ошибки разных видов: письмо помечаем, сотруднику — 502
            log.warning("customer_email_failed", customer_id=customer_id, error=str(exc))
            await _mark_failed(conn, email_id, str(exc) or exc.__class__.__name__)
            raise ApiError(502, "mail_failed", "Почтовый сервер не принял письмо. Попробуйте ещё раз позже.") from exc
        await _mark_sent(conn, email_id, message_id, customer_id, subject, user, followup_days)
        email = await conn.fetchrow("SELECT * FROM scoring.customer_emails WHERE id = $1", email_id)
        state = await actions.state(conn, customer_id)
    log.info("customer_email_sent", customer_id=customer_id, email_id=email_id, user=user)
    return {"email": email_out(email), "state": state}


# --- рассылка --------------------------------------------------------------------------------


async def _recipients(conn: asyncpg.Connection, req: CampaignRequest) -> tuple[list[int], int]:
    """(id получателей по порядку, сколько id не нашлось)."""
    if req.worklist:
        return await actions.queue_ids(conn, MAX_CAMPAIGN), 0
    ids = list(dict.fromkeys(i for i in req.customer_ids or [] if 0 < i <= BIGINT_MAX))
    found = await conn.fetch("SELECT customer_id FROM scoring.customer_scores WHERE customer_id = ANY($1::bigint[])", ids)
    known = {r["customer_id"] for r in found}
    return [i for i in ids if i in known], len(ids) - len(known)


async def _recent(conn: asyncpg.Connection, ids: list[int], skip_days: int) -> set[int]:
    if skip_days <= 0 or not ids:
        return set()
    rows = await conn.fetch(
        "SELECT DISTINCT customer_id FROM scoring.customer_emails WHERE customer_id = ANY($1::bigint[]) "
        "AND status IN ('queued', 'sending', 'sent') "
        "AND COALESCE(sent_at, created_at) > now() - make_interval(days => $2)",
        ids,
        skip_days,
    )
    return {r["customer_id"] for r in rows}


async def _plan(conn: asyncpg.Connection, req: CampaignRequest, company: str) -> dict[str, Any]:
    _check_template(req.template_code)
    clock = await conn.fetchrow(_NOW_SQL)
    ids, missing = await _recipients(conn, req)
    recent = await _recent(conn, ids, req.skip_days)
    customers = await _customers(conn, ids)
    templates = await mail_templates.load_all(conn)
    send: list[tuple[dict[str, Any], str, str, str]] = []
    skipped: list[tuple[dict[str, Any], str]] = []
    for cid in ids:
        customer = customers[cid]
        if cid in recent:
            skipped.append((customer, "recent"))
        elif not customer["email"]:
            skipped.append((customer, "no_email"))
        else:
            send.append((customer, *_compose(customer, templates, req.template_code, clock["ref"], company)))
    return {"send": send, "skipped": skipped, "missing": missing}


def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    used: dict[str, int] = {}
    for _, code, _, _ in plan["send"]:
        used[code] = used.get(code, 0) + 1
    sample = None
    if plan["send"]:
        customer, code, subject, text = plan["send"][0]
        sample = {"customer_id": customer["customer_id"], "to": customer["email"], "template_code": code,
                  "subject": subject, "text": text}
    return {
        "total": len(plan["send"]) + len(plan["skipped"]),
        "to_send": len(plan["send"]),
        "skipped_recent": sum(1 for _, why in plan["skipped"] if why == "recent"),
        "skipped_no_email": sum(1 for _, why in plan["skipped"] if why == "no_email"),
        "not_found": plan["missing"],
        "templates": [{"code": k, "count": v} for k, v in sorted(used.items(), key=lambda kv: -kv[1])],
        "sample": sample,
    }


async def preview_campaign(pool: asyncpg.Pool, req: CampaignRequest, config: MailConfig) -> dict[str, Any]:
    async with pool.acquire() as conn:
        plan = await _plan(conn, req, config.from_name)
    return {**_plan_summary(plan), "enabled": config.enabled}


_CAMPAIGN_SQL = """
SELECT c.id, c.template_code, c.skip_days, c.created_at, c.created_by, c.finished_at,
       count(e.id) AS total,
       count(e.id) FILTER (WHERE e.status = 'queued') AS queued,
       count(e.id) FILTER (WHERE e.status = 'sending') AS sending,
       count(e.id) FILTER (WHERE e.status = 'sent') AS sent,
       count(e.id) FILTER (WHERE e.status = 'failed') AS failed,
       count(e.id) FILTER (WHERE e.status = 'skipped') AS skipped
FROM scoring.mail_campaigns c LEFT JOIN scoring.customer_emails e ON e.campaign_id = c.id
"""


def campaign_out(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "template_code": row["template_code"],
        "skip_days": row["skip_days"],
        "created_at": iso_z(row["created_at"]),
        "created_by": row["created_by"],
        "finished_at": iso_z(row["finished_at"]),
        "status": "done" if row["finished_at"] is not None else "running",
        **{k: row[k] for k in ("total", "queued", "sending", "sent", "failed", "skipped")},
    }


async def create_campaign(
    pool: asyncpg.Pool, req: CampaignRequest, config: MailConfig, reply_to: str, user: str | None
) -> dict[str, Any]:
    _check_enabled(config)
    async with pool.acquire() as conn, conn.transaction():
        plan = await _plan(conn, req, config.from_name)
        if not plan["send"]:
            raise ApiError(422, "nothing_to_send", "Отправлять некому: все получатели недавно получали письмо или без email")
        campaign_id = await conn.fetchval(
            "INSERT INTO scoring.mail_campaigns (template_code, skip_days, created_by) VALUES ($1, $2, $3) RETURNING id",
            req.template_code,
            req.skip_days,
            user,
        )
        for customer, code, subject, text in plan["send"]:
            await _snapshot_insert(conn, customer, campaign_id=campaign_id, reply_to=reply_to, code=code,
                                   subject=subject, text=text, status="queued", user=user)
        for customer, why in plan["skipped"]:
            await _snapshot_insert(conn, customer, campaign_id=campaign_id, reply_to=reply_to, code=None,
                                   subject="", text="", status="skipped", user=user, skip_reason=why)
        row = await conn.fetchrow(_CAMPAIGN_SQL + " WHERE c.id = $1 GROUP BY c.id", campaign_id)
    log.info("mail_campaign_created", campaign_id=campaign_id, queued=row["queued"], user=user)
    return campaign_out(row)


async def get_campaign(pool: asyncpg.Pool, campaign_id: int) -> dict[str, Any]:
    if campaign_id < 1 or campaign_id > BIGINT_MAX:
        raise ApiError(404, "not_found", "Рассылка не найдена")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_CAMPAIGN_SQL + " WHERE c.id = $1 GROUP BY c.id", campaign_id)
    if row is None:
        raise ApiError(404, "not_found", "Рассылка не найдена")
    return campaign_out(row)


async def list_campaigns(pool: asyncpg.Pool, limit: int) -> dict[str, Any]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_CAMPAIGN_SQL + " GROUP BY c.id ORDER BY c.id DESC LIMIT $1", limit)
    return {"items": [campaign_out(r) for r in rows]}


# --- фоновая отправка ------------------------------------------------------------------------

_CLAIM_SQL = """
UPDATE scoring.customer_emails SET status = 'sending', attempts = attempts + 1
WHERE id IN (
    SELECT id FROM scoring.customer_emails WHERE status = 'queued'
    ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED
)
RETURNING id, customer_id, to_email, reply_to, subject, body, created_by
"""

_FINISH_SQL = """
UPDATE scoring.mail_campaigns c SET finished_at = now()
WHERE c.finished_at IS NULL AND NOT EXISTS (
    SELECT 1 FROM scoring.customer_emails e WHERE e.campaign_id = c.id AND e.status IN ('queued', 'sending')
)
"""


class MailWorker:
    """Отправляет письма рассылок из очереди не быстрее rate_per_s писем в секунду."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        config: MailConfig,
        *,
        rate_per_s: float,
        batch_size: int,
        interval_s: float,
        followup_days: int,
    ) -> None:
        self.pool = pool
        self.config = config
        self.min_gap = 1.0 / rate_per_s if rate_per_s > 0 else 0.0
        self.batch_size = batch_size
        self.interval_s = interval_s
        self.followup_days = followup_days
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_send = 0.0

    def start(self) -> None:
        if self.config.enabled and self._task is None:
            self._task = asyncio.create_task(self._run(), name="mail-worker")

    def kick(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def recover(self) -> None:
        """Письма, застрявшие в sending после падения реплики: не пересылаем, чтобы не задвоить."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE scoring.customer_emails SET status = 'failed', error = 'Отправка прервана перезапуском' "
                "WHERE status = 'sending' AND created_at < now() - $1::interval",
                STALE_SENDING,
            )
            await conn.execute(_FINISH_SQL)

    async def _run(self) -> None:
        with contextlib.suppress(Exception):
            await self.recover()
        while True:
            try:
                sent = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # база недоступна и т.п. — подождём и попробуем снова
                log.warning("mail_worker_error", error=str(exc))
                sent = 0
            if sent == 0:
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), self.interval_s)

    async def _throttle(self) -> None:
        wait = self._last_send + self.min_gap - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_send = time.monotonic()

    async def run_once(self) -> int:
        """Одна пачка. Возвращает, сколько писем обработано."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(_CLAIM_SQL, self.batch_size)
            for row in rows:
                await self._throttle()
                try:
                    message_id = await send_mail(self.config, _mail(row))
                except Exception as exc:
                    log.warning("campaign_email_failed", email_id=row["id"], error=str(exc))
                    await _mark_failed(conn, row["id"], str(exc) or exc.__class__.__name__)
                    continue
                await _mark_sent(
                    conn, row["id"], message_id, row["customer_id"], row["subject"], row["created_by"],
                    self.followup_days,
                )
            if rows:
                await conn.execute(_FINISH_SQL)
        return len(rows)


# --- эффективность ---------------------------------------------------------------------------

_STATS_SQL = """
WITH last AS (
    SELECT DISTINCT ON (customer_id) customer_id, risk_score_at_send, balance_at_send
    FROM scoring.customer_emails
    WHERE status = 'sent' AND sent_at > now() - make_interval(days => $1)
    ORDER BY customer_id, sent_at DESC
), judged AS (
    SELECT (cs.risk_score < l.risk_score_at_send) IS TRUE AS risk_down,
           (cs.balance > l.balance_at_send) IS TRUE AS topped_up
    FROM last l LEFT JOIN scoring.customer_scores cs USING (customer_id)
)
SELECT
    (SELECT count(*) FROM scoring.customer_emails
      WHERE status = 'sent' AND sent_at > now() - make_interval(days => $1)) AS sent,
    (SELECT count(*) FROM scoring.customer_emails
      WHERE status = 'failed' AND created_at > now() - make_interval(days => $1)) AS failed,
    (SELECT count(*) FROM scoring.customer_emails WHERE status IN ('queued', 'sending')) AS queued,
    (SELECT count(*) FROM judged) AS customers,
    (SELECT count(*) FROM judged WHERE risk_down) AS risk_down,
    (SELECT count(*) FROM judged WHERE topped_up) AS topped_up,
    (SELECT count(*) FROM judged WHERE risk_down OR topped_up) AS helped
"""


async def stats(pool: asyncpg.Pool, days: int) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_STATS_SQL, days)
    customers = row["customers"]
    return {
        "period_days": days,
        "sent": row["sent"],
        "failed": row["failed"],
        "queued": row["queued"],
        "customers": customers,
        "risk_down": row["risk_down"],
        "topped_up": row["topped_up"],
        "helped": row["helped"],
        "helped_share": round(row["helped"] / customers, 4) if customers else None,
    }
