"""Управление пользователями (только admin) и собственный профиль."""

# без `from __future__ import annotations`: FastAPI должен видеть локальные Annotated-зависимости

import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .accounts import clean_email
from .sessions import Session
from .users import UserRecord

log = logging.getLogger("auth.users")

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
MIN_PASSWORD = 8


def _email(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    cleaned = clean_email(value)
    if cleaned is None:
        raise ValueError("Это не похоже на адрес почты")
    return cleaned


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    display_name: str = Field(default="", max_length=128)
    email: str | None = Field(default=None, max_length=254)
    role: Literal["admin", "marketer", "analyst"] = "analyst"
    # пусто и sso_only=false — сгенерируем временный пароль и покажем один раз
    password: str | None = Field(default=None, max_length=256)
    sso_only: bool = False

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        v = v.strip()
        if not USERNAME_RE.match(v):
            raise ValueError("Логин: латиница, цифры и . _ @ -, начинается с буквы или цифры")
        return v

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        return _email(v)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=254)
    role: Literal["admin", "marketer", "analyst"] | None = None
    status: Literal["active", "blocked"] | None = None

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        return _email(v)


class PasswordReset(BaseModel):
    # пусто — сгенерировать временный
    password: str | None = Field(default=None, max_length=256)


class ProfileUpdate(BaseModel):
    email: str | None = Field(default=None, max_length=254)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        return _email(v)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


def temporary_password() -> str:
    return secrets.token_urlsafe(9)


def register_user_routes(
    app: FastAPI,
    *,
    ctx: Callable[[], Any],
    current_session: Callable[..., Awaitable[Session]],
    extract_token: Callable[[Request], tuple[str | None, str]],
    same_origin: Callable[[Request], bool],
    hasher: PasswordHasher,
    api_error: type[Exception],
) -> None:
    Ctx = Annotated[Any, Depends(ctx)]
    CurrentSession = Annotated[Session, Depends(current_session)]

    def fail(status: int, code: str, message: str) -> Exception:
        return api_error(status, code, message)

    def check_csrf(request: Request) -> None:
        _, kind = extract_token(request)
        if kind == "cookie" and not same_origin(request):
            raise fail(403, "csrf_rejected", "Запрос пришёл с чужого сайта")

    def check_password(password: str, username: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise fail(422, "weak_password", f"Пароль должен быть не короче {MIN_PASSWORD} символов")
        if password.lower() == username.lower():
            raise fail(422, "weak_password", "Пароль не должен совпадать с логином")

    async def admin_session(request: Request, st: Ctx, session: CurrentSession) -> Session:
        if "admin" not in session.roles:
            raise fail(403, "forbidden", "Управлять пользователями может только администратор")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            check_csrf(request)
        me = await st.repo.get_user(session.user_id)
        if me is None or not me.is_active:
            await st.sessions.revoke_user(session.user_id)
            raise fail(401, "unauthorized", "Нужно войти в систему")
        return session

    AdminSession = Annotated[Session, Depends(admin_session)]

    async def load(st, user_id: str) -> UserRecord:
        user = await st.repo.get_user(user_id)
        if user is None:
            raise fail(404, "user_not_found", "Пользователь не найден")
        return user

    async def guard_last_admin(st, user: UserRecord) -> None:
        if user.is_active and "admin" in user.roles and await st.repo.count_active_admins() <= 1:
            raise fail(409, "last_admin", "Это последний активный администратор: сначала назначьте другого")

    async def audit(st, session: Session, action: str, target: UserRecord | None, **details: Any) -> None:
        await st.repo.add_audit(session.username, action, target.username if target else None, details)
        log.info(
            "user admin action",
            extra={"fields": {"actor": session.username, "action": action, "target": target and target.username}},
        )

    # ---- admin --------------------------------------------------------------------------

    @app.get("/api/v1/auth/users")
    async def list_users(st: Ctx, session: AdminSession, q: str = ""):
        users = await st.repo.list_users(q[:128])
        return {"items": [u.public() for u in users], "total": len(users)}

    @app.post("/api/v1/auth/users", status_code=201)
    async def create_user(body: UserCreate, st: Ctx, session: AdminSession):
        if await st.repo.username_taken(body.username):
            raise fail(409, "username_taken", "Пользователь с таким логином уже есть")
        temp = None
        password_hash = None
        provider = "sso"
        if not body.sso_only:
            provider = "password"
            if body.password:
                check_password(body.password, body.username)
                password = body.password
            else:
                password = temp = temporary_password()
            password_hash = hasher.hash(password)
        user = await st.repo.create_user(
            UserRecord(
                id="",
                username=body.username,
                display_name=body.display_name.strip() or body.username,
                roles=[body.role],
                provider=provider,
                password_hash=password_hash,
                email=body.email,
                role_override=provider == "sso",
            )
        )
        await audit(st, session, "user.create", user, role=body.role, source=provider)
        result: dict = {"user": user.public()}
        if temp:
            result["temporary_password"] = temp
        return JSONResponse(result, status_code=201)

    @app.patch("/api/v1/auth/users/{user_id}")
    async def update_user(user_id: str, body: UserUpdate, st: Ctx, session: AdminSession):
        user = await load(st, user_id)
        sent = body.model_fields_set
        changes: dict = {}
        revoke = False
        if "display_name" in sent and body.display_name and body.display_name.strip() != user.display_name:
            changes["display_name"] = body.display_name.strip()
        if "email" in sent and body.email != user.email:
            changes["email"] = body.email
        if body.role and body.role != user.role:
            if user.id == session.user_id:
                raise fail(400, "self_change", "Свою роль поменять нельзя: попросите другого администратора")
            if body.role != "admin":
                await guard_last_admin(st, user)
            changes["roles"] = [body.role]
            changes["role_override"] = True
            revoke = True
        if body.status and (body.status == "active") != user.is_active:
            if user.id == session.user_id:
                raise fail(400, "self_change", "Себя заблокировать нельзя")
            if body.status == "blocked":
                await guard_last_admin(st, user)
            changes["is_active"] = body.status == "active"
            revoke = revoke or body.status == "blocked"
        if not changes:
            return {"user": user.public()}

        updated = await st.repo.update_user(user.id, **changes)
        if revoke:
            await st.sessions.revoke_user(user.id)
        logged = {k: v for k, v in changes.items() if k not in {"role_override"}}
        if "is_active" in logged:
            await audit(st, session, "user.block" if not logged.pop("is_active") else "user.unblock", updated)
        if "roles" in logged:
            await audit(st, session, "user.role", updated, role=logged.pop("roles")[0], previous=user.role)
        if logged:
            await audit(st, session, "user.update", updated, fields=sorted(logged))
        return {"user": updated.public()}

    @app.post("/api/v1/auth/users/{user_id}/password")
    async def reset_password(user_id: str, body: PasswordReset, st: Ctx, session: AdminSession):
        user = await load(st, user_id)
        if user.provider != "password":
            raise fail(400, "sso_user", "Пользователь входит через SSO, пароль ему задаёт провайдер входа")
        temp = None
        if body.password:
            check_password(body.password, user.username)
            password = body.password
        else:
            password = temp = temporary_password()
        await st.repo.update_user(user.id, password_hash=hasher.hash(password))
        await st.sessions.revoke_user(user.id)
        await audit(st, session, "user.password_reset", user, generated=temp is not None)
        return {"temporary_password": temp} if temp else Response(status_code=204)

    @app.delete("/api/v1/auth/users/{user_id}", status_code=204)
    async def delete_user(user_id: str, st: Ctx, session: AdminSession):
        user = await load(st, user_id)
        if user.id == session.user_id:
            raise fail(400, "self_change", "Себя удалить нельзя")
        await guard_last_admin(st, user)
        await st.repo.delete_user(user.id)
        await st.sessions.revoke_user(user.id)
        await audit(st, session, "user.delete", user, source=user.provider)
        return Response(status_code=204)

    @app.get("/api/v1/auth/audit")
    async def audit_log(st: Ctx, session: AdminSession, limit: int = 100):
        entries = await st.repo.list_audit(max(1, min(limit, 500)))
        return {"items": [e.public() for e in entries]}

    # ---- own profile --------------------------------------------------------------------

    @app.patch("/api/v1/auth/me")
    async def update_profile(body: ProfileUpdate, request: Request, st: Ctx, session: CurrentSession):
        check_csrf(request)
        user = await load(st, session.user_id)
        if "email" in body.model_fields_set and body.email != user.email:
            user = await st.repo.update_user(user.id, email=body.email) or user
            await st.repo.add_audit(session.username, "self.email", session.username, {})
        return {**session.public(), "email": user.email}

    @app.post("/api/v1/auth/me/password", status_code=204)
    async def change_password(body: PasswordChange, request: Request, st: Ctx, session: CurrentSession):
        check_csrf(request)
        user = await load(st, session.user_id)
        if user.provider != "password" or not user.password_hash:
            raise fail(400, "sso_user", "Вы входите через SSO: пароль меняется у провайдера входа")
        try:
            ok = hasher.verify(user.password_hash, body.current_password)
        except (VerificationError, InvalidHashError):
            ok = False
        if not ok:
            raise fail(400, "wrong_password", "Текущий пароль указан неверно")
        check_password(body.new_password, user.username)
        await st.repo.update_user(user.id, password_hash=hasher.hash(body.new_password))
        token, _ = extract_token(request)
        # остальные сессии (другие браузеры) закрываем, текущую оставляем
        await st.sessions.revoke_user(user.id, keep_token=token)
        await st.repo.add_audit(session.username, "self.password", session.username, {})
        return Response(status_code=204)
