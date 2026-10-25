"""Страница «Вход и регистрация»: какие способы входа включены и кого пускать через SSO (только admin).

Защита от того, чтобы администратор запер всех снаружи:
* хотя бы один способ входа остаётся включённым;
* выключить пароль можно, только находясь в панели через SSO (OIDC или SAML), который остаётся
  включённым, под SSO-учёткой администратора: раз он сейчас здесь, вход через SSO у него работает;
* последнего SSO-администратора при выключенном пароле нельзя заблокировать, удалить или понизить
  (это проверяет users_api);
* на крайний случай — AUTH_BREAK_GLASS_PASSWORD_LOGIN=1 в окружении auth.

Сессии, выданные способом, который выключили, сбрасываются сразу; при включённом аварийном ключе
парольные сессии администраторов остаются.
"""

# без `from __future__ import annotations`: FastAPI должен видеть локальные Annotated-зависимости

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .login_settings import METHODS, SSO_METHODS, LoginSettings, diff, normalize_domains
from .sessions import Session
from .users import UserRecord

log = logging.getLogger("auth.login_settings")

METHOD_NAMES = {"password": "вход по паролю", "oidc": "OIDC", "saml": "SAML"}


class LoginSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password_enabled: StrictBool | None = None
    oidc_enabled: StrictBool | None = None
    saml_enabled: StrictBool | None = None
    sso_auto_provision: StrictBool | None = None
    sso_default_role: Literal["none", "analyst", "marketer"] | None = None
    allowed_email_domains: list[str] | None = Field(default=None, max_length=200)

    @field_validator("allowed_email_domains")
    @classmethod
    def _domains(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else normalize_domains(v)


def register_login_settings_routes(
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
            raise api_error(403, "forbidden", "Способы входа настраивает только администратор")
        _, kind = extract_token(request)
        if request.method != "GET" and kind == "cookie" and not same_origin(request):
            raise api_error(403, "csrf_rejected", "Запрос пришёл с чужого сайта")
        me = await st.repo.get_user(session.user_id)
        if me is None or not me.is_active:
            await st.sessions.revoke_user(session.user_id)
            raise api_error(401, "unauthorized", "Нужно войти в систему")
        return session

    AdminSession = Annotated[Session, Depends(admin_session)]

    def password_blocker(st: Any, session: Session, me: UserRecord | None, target: LoginSettings) -> str | None:
        """Почему нельзя остаться без пароля при настройках target; None — можно."""
        policy = st.login_policy
        sso_on = [m for m in SSO_METHODS if policy.active(target, m)]
        if not sso_on:
            return "Сначала включите вход через SSO: без него и без пароля в панель никто не войдёт"
        names = " или ".join(METHOD_NAMES[m] for m in sso_on)
        if session.auth_method in SSO_METHODS and session.auth_method not in sso_on:
            return (
                f"Вы вошли через {METHOD_NAMES[session.auth_method]}, а этот способ выключается вместе с паролем. "
                f"Войдите через {names}, иначе потеряете доступ"
            )
        linked = (
            me is not None
            and me.is_active
            and "admin" in me.roles
            and me.provider == session.auth_method
            and session.auth_method in sso_on
        )
        if not linked:
            return (
                f"Чтобы выключить вход по паролю, войдите в панель через SSO ({names}) учётной записью "
                "администратора. Так мы убедимся, что вход через SSO у вас работает и доступ не потеряется"
            )
        return None

    async def view(st: Any, session: Session, value: LoginSettings) -> dict:
        policy = st.login_policy
        s = st.settings
        oidc_up, saml_up = await asyncio.gather(st.oidc.available(), st.saml.available())
        me = await st.repo.get_user(session.user_id)
        sso_active = tuple(m for m in SSO_METHODS if policy.active(value, m))
        return {
            **value.editable(),
            "updated_at": value.updated_at.isoformat() if value.updated_at else None,
            "updated_by": value.updated_by,
            "methods": {
                "password": {"configured": True, "available": True, "active": value.password_enabled},
                "oidc": {
                    "configured": policy.configured("oidc"),
                    "available": oidc_up,
                    "active": policy.active(value, "oidc"),
                    "display_name": s.oidc_display_name,
                },
                "saml": {
                    "configured": policy.configured("saml"),
                    "available": saml_up,
                    "active": policy.active(value, "saml"),
                    "display_name": s.saml_display_name,
                },
            },
            "break_glass": policy.break_glass,
            "session_method": session.auth_method,
            "password_disable_blocker": password_blocker(st, session, me, replace(value, password_enabled=False)),
            "sso_admins": await st.repo.count_active_admins(sso_active) if sso_active else 0,
        }

    @app.get("/api/v1/auth/login-settings")
    async def get_login_settings(st: Ctx, session: AdminSession):
        return await view(st, session, await st.login_policy.current(fresh=True))

    @app.patch("/api/v1/auth/login-settings")
    async def update_login_settings(body: LoginSettingsUpdate, request: Request, st: Ctx, session: AdminSession):
        policy = st.login_policy
        before = await policy.current(fresh=True)
        requested = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
        after = replace(before, **requested)

        async def reject(status: int, code: str, message: str) -> Exception:
            # попытки запереть вход тоже видны в журнале
            await st.repo.add_audit(
                session.username,
                "auth.settings.update",
                "login",
                {"error": code, "requested": requested},
                object_type="settings",
                success=False,
            )
            log.warning("login settings change rejected", extra={"fields": {"actor": session.username, "code": code}})
            return api_error(status, code, message)

        for method in SSO_METHODS:
            if after.enabled(method) != before.enabled(method) and not policy.configured(method):
                raise await reject(
                    409,
                    "provider_not_configured",
                    f"{METHOD_NAMES[method]} не настроен на сервере, поэтому переключить его здесь нельзя. "
                    f"Сначала задайте параметры {method.upper()}_* в окружении auth",
                )
        if not policy.active_methods(after):
            raise await reject(
                409, "no_login_methods", "Нельзя выключить все способы входа: должен остаться хотя бы один"
            )
        methods_changed = any(after.enabled(m) != before.enabled(m) for m in METHODS)
        if not after.password_enabled and methods_changed:
            me = await st.repo.get_user(session.user_id)
            blocker = password_blocker(st, session, me, after)
            if blocker:
                raise await reject(409, "sso_required", blocker)

        changes = diff(before, after)
        if not changes:
            return {**await view(st, session, before), "revoked_sessions": 0}

        saved = await policy.save(after, session.username)
        revoked = 0
        for method in METHODS:
            if policy.active(before, method) and not policy.active(saved, method):
                keep = None
                if method == "password" and policy.break_glass:
                    # аварийный ключ включён — администраторам по паролю всё равно можно
                    def keep(s: Session) -> bool:
                        return "admin" in s.roles

                revoked += await st.sessions.revoke_method(method, keep=keep)
        await st.repo.add_audit(
            session.username,
            "auth.settings.update",
            "login",
            {"changes": changes, "revoked_sessions": revoked},
            object_type="settings",
        )
        log.info(
            "login settings changed",
            extra={"fields": {"actor": session.username, "fields": sorted(changes), "revoked": revoked}},
        )
        return {**await view(st, session, saved), "revoked_sessions": revoked}
