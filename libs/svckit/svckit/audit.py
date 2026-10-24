"""Единый журнал действий (таблица audit.events, db/core/012_audit_reports.sql).

Каждый сервис пишет в журнал сам, в ту же базу, что и данные. Изменяющие запросы
(POST/PUT/PATCH/DELETE) записывает AuditMiddleware: кто (X-User-* от шлюза), что (правило
по методу и пути), с каким объектом, IP, request_id и тело запроса без секретов. Для того, что
делается не по HTTP-запросу (фоновая отправка отчёта), есть write_event.

Записываются успешные запросы (2xx) и отказы в доступе (403). Ошибки валидации и 5xx —
действие не состоялось, в журнал не идут. Если запись в журнал не удалась, ответ клиенту уже
ушёл: ошибка уходит в лог и в метрику audit_write_errors_total, чтобы её было видно.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import structlog
from prometheus_client import REGISTRY, CollectorRegistry, Counter

log = structlog.get_logger()

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SECRET_KEYS = ("password", "secret", "token", "authorization", "api_key", "apikey")
MAX_STRING = 300
MAX_DETAILS = 4000

INSERT_SQL = (
    "INSERT INTO audit.events (service, actor, actor_type, actor_role, action, object_type, object_id, "
    "success, ip, request_id, details) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::text::jsonb)"
)


@dataclass(frozen=True, slots=True)
class Actor:
    name: str
    type: str = "user"  # user | token | system | anonymous
    role: str | None = None
    ip: str | None = None
    request_id: str | None = None


SYSTEM = Actor("system", "system")


@dataclass(frozen=True, slots=True)
class Rule:
    method: str
    pattern: re.Pattern[str]
    action: str | None  # None — запрос не журналируем (предпросмотр, черновик)
    object_type: str | None = None
    object_group: str | None = None  # имя группы в pattern, из которой берётся id объекта
    body: bool = True  # класть ли тело запроса в details


def rule(
    method: str,
    path: str,
    action: str | None,
    object_type: str | None = None,
    *,
    object_group: str | None = None,
    body: bool = True,
) -> Rule:
    """path — регулярное выражение целиком, например r"/api/v1/customers/(?P<id>\\d+)/email"."""
    return Rule(method.upper(), re.compile(f"^{path}$"), action, object_type, object_group, body)


def skip(method: str, path: str) -> Rule:
    return rule(method, path, None)


def redact(value: Any, depth: int = 0) -> Any:
    """Тело запроса для журнала: без паролей и токенов, длинные строки обрезаны."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:50]:
            k = str(key)
            out[k] = "***" if any(s in k.lower() for s in SECRET_KEYS) else redact(item, depth + 1)
        return out
    if isinstance(value, list):
        items = [redact(v, depth + 1) for v in value[:50]]
        if len(value) > 50:
            items.append(f"… ещё {len(value) - 50}")
        return items
    if isinstance(value, str) and len(value) > MAX_STRING:
        return value[:MAX_STRING] + "…"
    return value


def details_json(details: dict[str, Any] | None) -> str:
    text = json.dumps(redact(details or {}), ensure_ascii=False, default=str)
    if len(text) > MAX_DETAILS:
        text = json.dumps({"truncated": True, "size": len(text)}, ensure_ascii=False)
    return text


def client_ip(headers: dict[str, str], peer: str | None) -> str | None:
    """IP клиента: X-Real-Ip шлюза, иначе первый в X-Forwarded-For, иначе адрес соединения."""
    real = headers.get("x-real-ip", "").strip()
    if real:
        return real[:64]
    found = headers.get("x-forwarded-for", "").split(",")[0].strip() or peer
    return found[:64] if found else None


def actor_from_headers(
    headers: dict[str, str], peer: str | None = None, request_id: str | None = None
) -> Actor:
    """Кто сделал запрос — по заголовкам, которые проставил ForwardAuth шлюза."""
    name = headers.get("x-user-name", "").strip()
    method = headers.get("x-auth-method", "").strip().lower()
    roles = [r.strip() for r in headers.get("x-user-roles", "").split(",") if r.strip()]
    kind = "token" if method == "token" else ("user" if name else "anonymous")
    return Actor(
        name=(name or "anonymous")[:160],
        type=kind,
        role=roles[0] if roles else None,
        ip=client_ip(headers, peer),
        request_id=request_id or headers.get("x-request-id") or None,
    )


async def write_event(
    db: Any,
    *,
    service: str,
    actor: Actor,
    action: str,
    object_type: str | None = None,
    object_id: Any = None,
    success: bool = True,
    details: dict[str, Any] | None = None,
) -> None:
    """Одна запись журнала. db — пул или соединение asyncpg (тогда — в его транзакции)."""
    await db.execute(
        INSERT_SQL,
        service,
        actor.name,
        actor.type,
        actor.role,
        action,
        object_type,
        None if object_id is None else str(object_id)[:200],
        success,
        actor.ip,
        actor.request_id,
        details_json(details),
    )


def make_counter(registry: CollectorRegistry | None = None) -> Counter:
    return Counter(
        "audit_write_errors_total",
        "Записи журнала действий, которые не удалось сохранить",
        registry=registry if registry is not None else REGISTRY,
    )


class AuditMiddleware:
    """ASGI-middleware: журналирует изменяющие запросы по правилам.

    Запрос, под который нет правила, тоже записывается (action «http.<метод>», путь в details):
    лучше лишняя строка, чем молча пропущенное действие. Чтобы не писать шум, на путь
    ставят skip().
    """

    def __init__(
        self,
        app: Any,
        *,
        service: str,
        rules: Iterable[Rule],
        pool: Callable[[Any], Any] | None = None,
        errors: Counter | None = None,
        body_limit: int = 16_384,
    ) -> None:
        self.app = app
        self.service = service
        self.rules = list(rules)
        self.pool = pool or (lambda asgi_app: getattr(getattr(asgi_app, "state", None), "pool", None))
        self.errors = errors
        self.body_limit = body_limit

    def match(self, method: str, path: str) -> tuple[Rule | None, re.Match[str] | None]:
        for r in self.rules:
            if r.method == method:
                m = r.pattern.match(path)
                if m:
                    return r, m
        return None, None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        found, m = self.match(method, path)
        if found is not None and found.action is None:
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        want_body = (found is None or found.body) and "json" in headers.get("content-type", "")
        chunks: list[bytes] = []
        size = 0
        status = 0

        async def receive_wrapper() -> Any:
            nonlocal size
            message = await receive()
            if want_body and message.get("type") == "http.request":
                part = message.get("body", b"")
                if size + len(part) <= self.body_limit:
                    chunks.append(part)
                size += len(part)
            return message

        async def send_wrapper(message: Any) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        await self.app(scope, receive_wrapper if want_body else receive, send_wrapper)

        if not (200 <= status < 300 or status == 403):
            return
        try:
            await self._record(scope, headers, found, m, method, path, status, chunks, size)
        except Exception as exc:
            if self.errors is not None:
                self.errors.inc()
            log.error("audit_write_failed", service=self.service, path=path, error=str(exc))

    async def _record(
        self,
        scope: Any,
        headers: dict[str, str],
        found: Rule | None,
        m: re.Match[str] | None,
        method: str,
        path: str,
        status: int,
        chunks: list[bytes],
        size: int,
    ) -> None:
        pool = self.pool(scope.get("app"))
        if pool is None:
            return
        peer = scope.get("client")[0] if scope.get("client") else None
        rid = (scope.get("state") or {}).get("request_id")
        actor = actor_from_headers(headers, peer, rid)
        details: dict[str, Any] = {}
        if chunks and size <= self.body_limit:
            try:
                parsed = json.loads(b"".join(chunks) or b"null")
            except ValueError:
                parsed = None
            if parsed is not None:
                details["request"] = parsed
        elif size > self.body_limit:
            details["request_bytes"] = size
        if status == 403:
            details["status"] = 403
        if found is None:
            action = f"http.{method.lower()}"
            details["path"] = path
            object_type = object_id = None
        else:
            action = found.action or ""
            object_type = found.object_type
            object_id = m.group(found.object_group) if (m and found.object_group) else None
        await write_event(
            pool,
            service=self.service,
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=object_id,
            success=status != 403,
            details=details,
        )
