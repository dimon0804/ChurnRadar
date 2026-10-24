"""Управление API-токенами (только admin, только из панели — сессией, не другим токеном)."""

# без `from __future__ import annotations`: FastAPI должен видеть локальные Annotated-зависимости

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .sessions import Session
from .tokens import parse_networks

log = logging.getLogger("auth.tokens")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    role: Literal["admin", "marketer", "analyst"] = "analyst"
    # пусто — бессрочный (не советуем, но интеграции бывают и такие)
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)
    allowed_ips: list[str] = Field(default_factory=list, max_length=50)
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=100_000)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not NAME_RE.match(v):
            raise ValueError("Имя токена: латиница, цифры и . _ -, до 64 символов, например aeza-billing")
        return v

    @field_validator("allowed_ips")
    @classmethod
    def _ips(cls, v: list[str]) -> list[str]:
        return parse_networks(v)


def register_token_routes(
    app: FastAPI,
    *,
    ctx: Callable[[], Any],
    current_session: Callable[..., Awaitable[Session]],
    extract_token: Callable[[Request], tuple[str | None, str]],
    same_origin: Callable[[Request], bool],
    api_error: type[Exception],
) -> None:
    Ctx = Annotated[Any, Depends(ctx)]
    CurrentSession = Annotated[Session, Depends(current_session)]

    async def admin_session(request: Request, st: Ctx, session: CurrentSession) -> Session:
        if "admin" not in session.roles:
            raise api_error(403, "forbidden", "API-токенами управляет только администратор")
        _, kind = extract_token(request)
        if request.method != "GET" and kind == "cookie" and not same_origin(request):
            raise api_error(403, "csrf_rejected", "Запрос пришёл с чужого сайта")
        me = await st.repo.get_user(session.user_id)
        if me is None or not me.is_active:
            await st.sessions.revoke_user(session.user_id)
            raise api_error(401, "unauthorized", "Нужно войти в систему")
        return session

    AdminSession = Annotated[Session, Depends(admin_session)]

    @app.get("/api/v1/auth/tokens")
    async def list_tokens(st: Ctx, session: AdminSession):
        items = await st.tokens.repo.list()
        return {"items": [t.public() for t in items]}

    @app.post("/api/v1/auth/tokens", status_code=201)
    async def create_token(body: TokenCreate, st: Ctx, session: AdminSession):
        if await st.tokens.repo.name_taken(body.name):
            raise api_error(409, "token_name_taken", "Действующий токен с таким именем уже есть")
        record, secret = await st.tokens.issue(
            name=body.name,
            role=body.role,
            created_by=session.username,
            expires_in_days=body.expires_in_days,
            allowed_ips=body.allowed_ips,
            rate_limit_per_min=body.rate_limit_per_min,
        )
        await st.repo.add_audit(
            session.username,
            "token.create",
            record.name,
            {
                "role": record.role,
                "expires_at": record.public()["expires_at"],
                "allowed_ips": record.allowed_ips,
                "prefix": record.prefix,
            },
            object_type="token",
        )
        log.info("api token created", extra={"fields": {"name": record.name, "by": session.username}})
        # сам токен — только в этом ответе: в базе лежит его хеш
        return JSONResponse({"token": record.public(), "secret": secret}, status_code=201)

    @app.delete("/api/v1/auth/tokens/{token_id}", status_code=204)
    async def revoke_token(token_id: str, st: Ctx, session: AdminSession):
        record = await st.tokens.repo.get(token_id)
        if record is None:
            raise api_error(404, "token_not_found", "Токен не найден")
        if record.revoked_at is None:
            await st.tokens.revoke(record.id, session.username)
            await st.repo.add_audit(
                session.username, "token.revoke", record.name, {"prefix": record.prefix}, object_type="token"
            )
            log.info("api token revoked", extra={"fields": {"name": record.name, "by": session.username}})
        return Response(status_code=204)
