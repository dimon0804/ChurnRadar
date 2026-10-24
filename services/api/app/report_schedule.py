"""Рассылка еженедельного отчёта: настройки (кому, в какой день и час) и фоновый отправщик.

Отчёт уходит письмом: короткая сводка в теле и PDF во вложении (графики — в PDF, почтовые
клиенты SVG не показывают). Реплик api несколько: неделю «забирает» та, что первой обновила
last_sent_on, остальные видят, что отчёт уже ушёл.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg
import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator
from svckit.audit import SYSTEM, write_event
from svckit.errors import ApiError
from svckit.mail import Attachment, Mail, MailConfig, send_mail

from app import report_pdf, reports

log = structlog.get_logger()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_RECIPIENTS = 20
_SELECT = (
    "SELECT enabled, weekday, send_time, timezone, recipients, last_sent_on, last_sent_at, last_error, "
    "updated_at, updated_by FROM scoring.report_settings WHERE id = 1"
)


def _recipients(values: list[str]) -> list[str]:
    out: list[str] = []
    for raw in values:
        email = raw.strip()
        if not email:
            continue
        if len(email) > 254 or not EMAIL_RE.match(email):
            raise ValueError(f"«{email}» — не похоже на адрес почты")
        if email.lower() not in (e.lower() for e in out):
            out.append(email)
    if len(out) > MAX_RECIPIENTS:
        raise ValueError(f"Не больше {MAX_RECIPIENTS} получателей")
    return out


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    weekday: int = Field(ge=1, le=7, description="ISO: 1 — понедельник, 7 — воскресенье")
    send_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="Europe/Moscow", max_length=64)
    recipients: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("recipients")
    @classmethod
    def _check_recipients(cls, v: list[str]) -> list[str]:
        return _recipients(v)

    @field_validator("timezone")
    @classmethod
    def _check_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Неизвестный часовой пояс «{v}»") from exc
        return v


class SendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # пусто — получатели из расписания
    recipients: list[str] | None = Field(default=None, max_length=50)

    @field_validator("recipients")
    @classmethod
    def _check_recipients(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _recipients(v)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def settings_of(row: Any) -> dict[str, Any]:
    if row is None:
        return {
            "enabled": False, "weekday": 1, "send_time": "09:00", "timezone": "Europe/Moscow", "recipients": [],
            "last_sent_at": None, "last_error": None, "updated_at": None, "updated_by": None,
        }
    return {
        "enabled": row["enabled"],
        "weekday": row["weekday"],
        "send_time": row["send_time"].strftime("%H:%M"),
        "timezone": row["timezone"],
        "recipients": list(row["recipients"]),
        "last_sent_at": _iso(row["last_sent_at"]),
        "last_error": row["last_error"],
        "updated_at": _iso(row["updated_at"]),
        "updated_by": row["updated_by"],
    }


async def get_schedule(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return settings_of(await conn.fetchrow(_SELECT))


async def put_schedule(pool: asyncpg.Pool, update: ScheduleUpdate, user_name: str | None) -> dict[str, Any]:
    if update.enabled and not update.recipients:
        raise ApiError(422, "validation_error", "Укажите хотя бы одного получателя отчёта")
    hh, mm = (int(x) for x in update.send_time.split(":"))
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO scoring.report_settings (id, enabled, weekday, send_time, timezone, recipients, "
            "updated_at, updated_by) VALUES (1, $1, $2, $3, $4, $5, now(), $6) "
            "ON CONFLICT (id) DO UPDATE SET enabled = EXCLUDED.enabled, weekday = EXCLUDED.weekday, "
            "send_time = EXCLUDED.send_time, timezone = EXCLUDED.timezone, recipients = EXCLUDED.recipients, "
            "updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by",
            update.enabled, update.weekday, time(hh, mm), update.timezone, update.recipients, user_name,
        )
        return settings_of(await conn.fetchrow(_SELECT))


def due_date(row: Any, now: datetime) -> Any:
    """Дата недели в часовом поясе расписания, если отчёт пора слать, иначе None."""
    if row is None or not row["enabled"] or not row["recipients"]:
        return None
    try:
        local = now.astimezone(ZoneInfo(row["timezone"]))
    except (ZoneInfoNotFoundError, ValueError):
        local = now.astimezone(UTC)
    if local.isoweekday() != row["weekday"] or local.time() < row["send_time"]:
        return None
    if row["last_sent_on"] == local.date():
        return None
    return local.date()


def build_mail(report: dict[str, Any], pdf: bytes, to: str) -> Mail:
    k = report["kpi"]
    text = (
        f"Еженедельный отчёт ChurnRadar за {report['period_from']} — {report['period_to']}.\n\n"
        f"Ожидаемая потеря: {reports.money(k['expected_loss'])}\n"
        f"Клиентов под высоким риском: {k['high']}\n"
        f"MRR: {reports.money(k['mrr'])}, ARPU: {reports.money(k['arpu'])}, LTV: {reports.money(k['ltv'])}\n\n"
        + "\n".join(reports.done_lines(report))
        + "\n\nПодробности и графики — в PDF во вложении."
    )
    return Mail(
        to=to,
        subject=f"ChurnRadar: отчёт за неделю, потеря {reports.money(k['expected_loss'])}",
        text=text,
        html=reports.render_html(report, for_email=True),
        attachments=[Attachment(reports.filename(report, "pdf"), pdf, "application/pdf")],
    )


async def send_report(pool: asyncpg.Pool, cache: Any, mail_config: MailConfig, recipients: list[str]) -> list[str]:
    """Собирает отчёт и шлёт каждому. Возвращает адреса, куда ушло; ошибка SMTP — исключение."""
    report = await reports.collect(pool, cache)
    pdf = await asyncio.to_thread(report_pdf.render_pdf, report)
    sent: list[str] = []
    for to in recipients:
        await send_mail(mail_config, build_mail(report, pdf, to))
        sent.append(to)
    return sent


class ReportScheduler:
    """Раз в минуту проверяет расписание; отправляет одна реплика, остальные видят last_sent_on."""

    def __init__(self, pool: Any, cache: Any, mail_config: MailConfig, *, interval_s: float = 60.0) -> None:
        self.pool = pool
        self.cache = cache
        self.mail_config = mail_config
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="report-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def run_once(self, now: datetime | None = None) -> list[str] | None:
        now = now or datetime.now(UTC)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT)
            day = due_date(row, now)
            if day is None:
                return None
            claimed = await conn.fetchval(
                "UPDATE scoring.report_settings SET last_sent_on = $1 "
                "WHERE id = 1 AND last_sent_on IS DISTINCT FROM $1 RETURNING 1",
                day,
            )
        if not claimed:
            return None
        recipients = list(row["recipients"])
        try:
            sent = await send_report(self.pool, self.cache, self.mail_config, recipients)
        except Exception as exc:
            # неделю не перезапускаем, чтобы не слать письма каждую минуту: админ увидит ошибку
            # в настройках и отправит вручную
            log.error("weekly_report_failed", error=str(exc))
            await self.pool.execute(
                "UPDATE scoring.report_settings SET last_error = $1 WHERE id = 1", str(exc)[:500]
            )
            await write_event(self.pool, service="api", actor=SYSTEM, action="report.sent", object_type="report",
                              success=False, details={"recipients": recipients, "error": str(exc)[:300]})
            return []
        await self.pool.execute(
            "UPDATE scoring.report_settings SET last_sent_at = now(), last_error = NULL WHERE id = 1"
        )
        await write_event(self.pool, service="api", actor=SYSTEM, action="report.sent", object_type="report",
                          details={"recipients": sent, "week": day.isoformat()})
        log.info("weekly_report_sent", recipients=len(sent))
        return sent

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
                pass  # миграции ещё идут
            except Exception as exc:
                log.warning("report_scheduler_error", error=str(exc))
            await asyncio.sleep(self.interval_s)
