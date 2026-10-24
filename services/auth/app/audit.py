"""Запись в единый журнал действий audit.events (db/core/012_audit_reports.sql).

auth пишет туда вход, выход, неудачные попытки, действия с пользователями и API-токенами.
IP и request_id берутся из контекста запроса (AuditContextMiddleware), так что вызывающему
коду достаточно сказать, кто и что сделал.

Таблицу создаёт мигратор ядра (core-migrate), auth запускается после него. Если таблицы всё же
нет или база не ответила, вход не ломаем: ошибка уходит в лог и в метрику
audit_write_errors_total.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

from prometheus_client import Counter

from .observability import request_id_var

log = logging.getLogger("auth.audit")

client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)

AUDIT_ERRORS = Counter("audit_write_errors_total", "Записи журнала действий, которые не удалось сохранить")

INSERT_SQL = (
    "INSERT INTO audit.events (service, actor, actor_type, actor_role, action, object_type, object_id, "
    "success, ip, request_id, details) VALUES ('auth', $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::text::jsonb)"
)

SELECT_USER_ACTIONS = (
    "SELECT id, actor, action, object_id, details::text AS details, at FROM audit.events "
    "WHERE service = 'auth' AND object_type = 'user' AND (action LIKE 'user.%' OR action LIKE 'self.%') "
    "ORDER BY at DESC, id DESC LIMIT $1"
)


def request_ip(headers: Any, peer: str | None) -> str | None:
    """IP клиента: X-Real-Ip шлюза, иначе первый адрес X-Forwarded-For, иначе адрес соединения."""
    real = (headers.get("x-real-ip") or "").strip()
    if real:
        return real[:64]
    found = (headers.get("x-forwarded-for") or "").split(",")[0].strip() or peer
    return found[:64] if found else None


class AuditContextMiddleware:
    """Кладёт IP клиента в контекст: его подхватывает каждая запись журнала в этом запросе."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        peer = scope.get("client")[0] if scope.get("client") else None
        token = client_ip_var.set(request_ip(headers, peer))
        try:
            await self.app(scope, receive, send)
        finally:
            client_ip_var.reset(token)


async def write(
    pool: Any,
    *,
    actor: str,
    action: str,
    object_type: str | None = None,
    object_id: str | None = None,
    details: dict | None = None,
    actor_type: str = "user",
    actor_role: str | None = None,
    success: bool = True,
    ip: str | None = None,
) -> None:
    rid = request_id_var.get()
    try:
        await pool.execute(
            INSERT_SQL,
            actor[:160],
            actor_type,
            actor_role,
            action,
            object_type,
            object_id[:200] if object_id else None,
            success,
            ip or client_ip_var.get(),
            None if rid in ("", "-") else rid,
            json.dumps(details or {}, ensure_ascii=False, default=str),
        )
    except Exception as exc:  # журнал не должен ронять вход
        AUDIT_ERRORS.inc()
        log.error("audit write failed", extra={"fields": {"action": action, "error": str(exc)}})
