"""Журнал действий: правила журналирования запросов api и выдача журнала (страница, CSV).

Пишут в audit.events все сервисы (svckit.audit, в auth — свой модуль), читает api.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import asyncpg
from svckit.audit import rule, skip

# что именно сделали в api — по методу и пути; черновики и предпросмотры ничего не меняют
API_RULES = [
    rule("PUT", r"/api/v1/customers/(?P<id>\d+)/action", "customer.action.set", "customer", object_group="id"),
    rule("DELETE", r"/api/v1/customers/(?P<id>\d+)/action", "customer.action.clear", "customer", object_group="id"),
    skip("POST", r"/api/v1/customers/\d+/email/draft"),
    rule("POST", r"/api/v1/customers/(?P<id>\d+)/email", "mail.send", "customer", object_group="id"),
    rule("PUT", r"/api/v1/mail/templates/(?P<code>[^/]+)", "mail.template.update", "mail_template", object_group="code"),
    rule(
        "POST", r"/api/v1/mail/templates/(?P<code>[^/]+)/reset", "mail.template.reset", "mail_template",
        object_group="code",
    ),
    rule("PUT", r"/api/v1/mail/settings", "mail.settings.update", "settings"),
    skip("POST", r"/api/v1/mail/preview/[^/]+"),
    skip("POST", r"/api/v1/mail/campaigns/preview"),
    rule("POST", r"/api/v1/mail/campaigns", "mail.campaign.create", "campaign"),
    rule("PUT", r"/api/v1/analytics/marketing/settings", "marketing.settings.update", "settings"),
    skip("POST", r"/api/v1/model/settings/preview"),
    rule("PATCH", r"/api/v1/model/settings", "model.settings.update", "model"),
    rule("POST", r"/api/v1/model/settings/reset", "model.settings.reset", "model"),
    rule("PUT", r"/api/v1/simulator", "simulator.command", "simulator"),
    skip("POST", r"/api/v1/playbooks/preview"),
    rule("POST", r"/api/v1/playbooks", "playbook.create", "playbook"),
    rule("PUT", r"/api/v1/playbooks/(?P<id>[^/]+)", "playbook.update", "playbook", object_group="id"),
    rule("DELETE", r"/api/v1/playbooks/(?P<id>[^/]+)", "playbook.delete", "playbook", object_group="id"),
    rule("POST", r"/api/v1/playbooks/(?P<id>[^/]+)/run", "playbook.run", "playbook", object_group="id"),
    rule("PUT", r"/api/v1/reports/schedule", "report.schedule.update", "report"),
    rule("POST", r"/api/v1/reports/weekly/send", "report.send", "report"),
]

ACTION_TITLES = {
    "auth.login": "Вход",
    "auth.login_failed": "Неудачный вход",
    "auth.logout": "Выход",
    "user.create": "Создан пользователь",
    "user.update": "Изменён пользователь",
    "user.role": "Изменена роль",
    "user.block": "Пользователь заблокирован",
    "user.unblock": "Пользователь разблокирован",
    "user.delete": "Пользователь удалён",
    "user.password_reset": "Сброшен пароль",
    "self.email": "Изменена своя почта",
    "self.password": "Изменён свой пароль",
    "token.create": "Создан API-токен",
    "token.revoke": "Отозван API-токен",
    "customer.action.set": "Отметка по клиенту",
    "customer.action.clear": "Снята отметка по клиенту",
    "mail.send": "Письмо клиенту",
    "mail.template.update": "Изменён шаблон письма",
    "mail.template.reset": "Шаблон письма сброшен",
    "mail.campaign.create": "Запущена рассылка",
    "marketing.settings.update": "Изменены маржа и CAC",
    "model.settings.update": "Изменены настройки модели",
    "model.settings.reset": "Настройки модели сброшены",
    "simulator.command": "Команда симулятору",
    "source.snapshot.upload": "Загружен снимок данных",
    "source.events.push": "Переданы события",
    "connector.create": "Добавлен коннектор",
    "connector.update": "Изменён коннектор",
    "connector.delete": "Удалён коннектор",
    "connector.run": "Коннектор запущен вручную",
    "connector.reset_cursor": "Сброшен курсор коннектора",
    "source.metrica.client_ids": "Загружены client id Метрики",
    "alerts.preferences.update": "Изменены настройки уведомлений",
    "alerts.preferences.reset": "Сброшены настройки уведомлений",
    "alerts.test": "Тестовое уведомление",
    "alerts.telegram.link": "Привязан Telegram",
    "alerts.telegram.unlink": "Отвязан Telegram",
    "playbook.create": "Создан сценарий удержания",
    "playbook.update": "Изменён сценарий удержания",
    "playbook.delete": "Удалён сценарий удержания",
    "playbook.run": "Сценарий запущен вручную",
    "report.schedule.update": "Изменено расписание отчёта",
    "report.send": "Отчёт отправлен вручную",
    "report.sent": "Отчёт отправлен по расписанию",
}

COLUMNS = "id, at, service, actor, actor_type, actor_role, action, object_type, object_id, success, ip, request_id, " \
    "details::text AS details"
EXPORT_LIMIT = 50_000

CSV_HEADER = [
    "Время (UTC)", "Сервис", "Кто", "Тип", "Роль", "Действие", "Код действия", "Объект", "ID объекта",
    "Успешно", "IP", "request_id", "Подробности",
]


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def item(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "at": _iso(row["at"]),
        "service": row["service"],
        "actor": row["actor"],
        "actor_type": row["actor_type"],
        "actor_role": row["actor_role"],
        "action": row["action"],
        "action_title": ACTION_TITLES.get(row["action"], row["action"]),
        "object_type": row["object_type"],
        "object_id": row["object_id"],
        "success": row["success"],
        "ip": row["ip"],
        "request_id": row["request_id"],
        "details": json.loads(row["details"]) if row["details"] else {},
    }


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def where(
    *,
    q: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    actor_type: str | None = None,
    object_type: str | None = None,
    service: str | None = None,
    success: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[str, list[Any]]:
    conds: list[str] = []
    args: list[Any] = []

    def add(sql: str, value: Any) -> None:
        args.append(value)
        conds.append(sql.replace("$?", f"${len(args)}"))

    if q and q.strip():
        add(
            "(actor ILIKE $? OR action ILIKE $? OR coalesce(object_id, '') ILIKE $? OR coalesce(ip, '') ILIKE $? "
            "OR coalesce(request_id, '') ILIKE $?)",
            f"%{_escape_like(q.strip())}%",
        )
    if actor and actor.strip():
        add("lower(actor) = lower($?)", actor.strip())
    if action and action.strip():
        # «user» — все действия с пользователями, «user.create» — только создание
        add("(action = $? OR action LIKE $?)", action.strip())
        n = len(args)
        args.append(_escape_like(action.strip()) + ".%")
        conds[-1] = f"(action = ${n} OR action LIKE ${n + 1})"
    if actor_type:
        add("actor_type = $?", actor_type)
    if object_type:
        add("object_type = $?", object_type)
    if service:
        add("service = $?", service)
    if success is not None:
        add("success = $?", success)
    if date_from:
        add("at >= $?", datetime.combine(date_from, time.min, UTC))
    if date_to:
        add("at < $?", datetime.combine(date_to + timedelta(days=1), time.min, UTC))
    return (" WHERE " + " AND ".join(conds)) if conds else "", args


async def list_events(
    pool: asyncpg.Pool, filters: dict[str, Any], page: int, page_size: int
) -> dict[str, Any]:
    sql_where, args = where(**filters)
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM audit.events{sql_where}", *args)
        rows = await conn.fetch(
            f"SELECT {COLUMNS} FROM audit.events{sql_where} ORDER BY at DESC, id DESC "
            f"LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
            *args, page_size, (page - 1) * page_size,
        )
        actions = await conn.fetch("SELECT DISTINCT action FROM audit.events ORDER BY action LIMIT 200")
    return {
        "items": [item(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "actions": [{"code": r["action"], "title": ACTION_TITLES.get(r["action"], r["action"])} for r in actions],
    }


async def export_csv(pool: asyncpg.Pool, filters: dict[str, Any]) -> bytes:
    sql_where, args = where(**filters)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {COLUMNS} FROM audit.events{sql_where} ORDER BY at DESC, id DESC LIMIT {EXPORT_LIMIT}", *args
        )
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for r in rows:
        writer.writerow([
            _iso(r["at"]), r["service"], r["actor"], r["actor_type"], r["actor_role"] or "",
            ACTION_TITLES.get(r["action"], r["action"]), r["action"], r["object_type"] or "", r["object_id"] or "",
            "да" if r["success"] else "нет", r["ip"] or "", r["request_id"] or "",
            r["details"] if r["details"] not in (None, "{}") else "",
        ])
    # BOM и «;» — русский Excel открывает без мастера импорта
    return ("﻿" + buf.getvalue()).encode("utf-8")


def export_filename(now: datetime) -> str:
    return f"churnradar-audit-{now.astimezone(UTC):%Y-%m-%d}.csv"
