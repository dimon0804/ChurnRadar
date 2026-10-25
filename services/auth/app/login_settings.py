"""Способы входа и регистрация через SSO: что включил администратор (auth.login_settings).

Настройки лежат поверх окружения: OIDC/SAML, не настроенные в env, выключены всегда, а включить
их из панели нельзя. Читаются на каждом входе и на каждом /internal/verify, поэтому держим
их в памяти несколько секунд; своё же изменение реплика видит сразу, остальные — через TTL.

Аварийный ключ AUTH_BREAK_GLASS_PASSWORD_LOGIN=1 пускает по паролю администраторов, что бы ни
было записано в базе: на случай, если IdP лёг, а пароль уже выключен.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from .config import Settings

log = logging.getLogger("auth.login_settings")

METHODS = ("password", "oidc", "saml")
SSO_METHODS = ("oidc", "saml")
SSO_DEFAULT_ROLES = ("none", "analyst", "marketer")
MAX_DOMAINS = 50
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
EDITABLE = (
    "password_enabled",
    "oidc_enabled",
    "saml_enabled",
    "sso_auto_provision",
    "sso_default_role",
    "allowed_email_domains",
)


def normalize_domain(value: str) -> str:
    """«@Example.COM » -> «example.com»; ValueError, если это не домен."""
    domain = value.strip().lower().removeprefix("@").rstrip(".")
    if not DOMAIN_RE.match(domain):
        raise ValueError(f"«{value.strip()}» не похоже на домен почты, пример: example.com")
    return domain


def normalize_domains(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for raw in values:
        if raw.strip():
            seen[normalize_domain(raw)] = None
    if len(seen) > MAX_DOMAINS:
        raise ValueError(f"Доменов не больше {MAX_DOMAINS}")
    return list(seen)


def email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower().rstrip(".") or None


@dataclass
class LoginSettings:
    password_enabled: bool = True
    oidc_enabled: bool = True
    saml_enabled: bool = True
    sso_auto_provision: bool = True
    sso_default_role: str = "none"
    allowed_email_domains: list[str] = field(default_factory=list)
    updated_at: datetime | None = None
    updated_by: str | None = None

    def enabled(self, method: str) -> bool:
        return bool(getattr(self, f"{method}_enabled"))

    @property
    def default_roles(self) -> list[str]:
        """Роль новому SSO-пользователю, если группы IdP ничего не дали; [] — не пускаем."""
        return [] if self.sso_default_role == "none" else [self.sso_default_role]

    def domain_allowed(self, email: str | None) -> bool:
        """Можно ли автосоздать пользователя с такой почтой. Пустой список доменов — любой."""
        if not self.allowed_email_domains:
            return True
        return email_domain(email) in self.allowed_email_domains

    def editable(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in EDITABLE}


def initial_settings(settings: Settings) -> LoginSettings:
    """Первая запись: всё включено, роль по умолчанию — из SSO_DEFAULT_ROLE (если она допустима)."""
    role = (settings.sso_default_role or "").strip().lower() or "none"
    if role not in SSO_DEFAULT_ROLES:
        log.warning(
            "SSO_DEFAULT_ROLE ignored: only analyst or marketer can be given automatically",
            extra={"fields": {"value": role}},
        )
        role = "none"
    return LoginSettings(sso_default_role=role)


class LoginSettingsRepo(Protocol):
    async def ensure(self, initial: LoginSettings) -> LoginSettings: ...

    async def get(self) -> LoginSettings | None: ...

    async def save(self, value: LoginSettings, by: str) -> LoginSettings: ...


def _row(row: Any) -> LoginSettings:
    return LoginSettings(
        password_enabled=row["password_enabled"],
        oidc_enabled=row["oidc_enabled"],
        saml_enabled=row["saml_enabled"],
        sso_auto_provision=row["sso_auto_provision"],
        sso_default_role=row["sso_default_role"],
        allowed_email_domains=list(row["allowed_email_domains"]),
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
    )


class PostgresLoginSettingsRepo:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def ensure(self, initial: LoginSettings) -> LoginSettings:
        await self.pool.execute(
            "INSERT INTO auth.login_settings (id, sso_default_role) VALUES (1, $1) ON CONFLICT (id) DO NOTHING",
            initial.sso_default_role,
        )
        found = await self.get()
        return found or initial

    async def get(self) -> LoginSettings | None:
        row = await self.pool.fetchrow("SELECT * FROM auth.login_settings WHERE id = 1")
        return _row(row) if row else None

    async def save(self, value: LoginSettings, by: str) -> LoginSettings:
        row = await self.pool.fetchrow(
            """
            INSERT INTO auth.login_settings (id, password_enabled, oidc_enabled, saml_enabled, sso_auto_provision,
                                             sso_default_role, allowed_email_domains, updated_at, updated_by)
            VALUES (1, $1, $2, $3, $4, $5, $6, now(), $7)
            ON CONFLICT (id) DO UPDATE SET
                password_enabled = EXCLUDED.password_enabled,
                oidc_enabled = EXCLUDED.oidc_enabled,
                saml_enabled = EXCLUDED.saml_enabled,
                sso_auto_provision = EXCLUDED.sso_auto_provision,
                sso_default_role = EXCLUDED.sso_default_role,
                allowed_email_domains = EXCLUDED.allowed_email_domains,
                updated_at = now(),
                updated_by = EXCLUDED.updated_by
            RETURNING *
            """,
            value.password_enabled,
            value.oidc_enabled,
            value.saml_enabled,
            value.sso_auto_provision,
            value.sso_default_role,
            list(value.allowed_email_domains),
            by[:160],
        )
        return _row(row)


@dataclass
class MemoryLoginSettingsRepo:
    """Для тестов и локального запуска без Postgres."""

    value: LoginSettings | None = None

    async def ensure(self, initial: LoginSettings) -> LoginSettings:
        if self.value is None:
            self.value = replace(initial, allowed_email_domains=list(initial.allowed_email_domains))
        return replace(self.value)

    async def get(self) -> LoginSettings | None:
        return replace(self.value) if self.value else None

    async def save(self, value: LoginSettings, by: str) -> LoginSettings:
        self.value = replace(
            value,
            allowed_email_domains=list(value.allowed_email_domains),
            updated_at=datetime.now(timezone.utc),
            updated_by=by,
        )
        return replace(self.value)


class LoginPolicy:
    """Действующие правила входа: настройки из базы плюс то, что настроено в окружении."""

    def __init__(self, repo: LoginSettingsRepo, settings: Settings, ttl_s: float = 5.0) -> None:
        self.repo = repo
        self.settings = settings
        self.ttl_s = ttl_s
        self._cached: tuple[float, LoginSettings] | None = None

    @property
    def break_glass(self) -> bool:
        return self.settings.auth_break_glass_password_login

    def configured(self, method: str) -> bool:
        if method == "password":
            return True
        return bool(getattr(self.settings, f"{method}_enabled", False))

    async def load(self) -> LoginSettings:
        """При старте: завести запись, если её ещё нет."""
        value = await self.repo.ensure(initial_settings(self.settings))
        self._remember(value)
        if self.break_glass:
            log.warning("AUTH_BREAK_GLASS_PASSWORD_LOGIN is on: administrators may sign in with a password")
        return value

    def invalidate(self) -> None:
        """Забыть закэшированное: следующий запрос перечитает базу."""
        self._cached = None

    def _remember(self, value: LoginSettings) -> None:
        self._cached = (time.monotonic(), value)

    async def current(self, *, fresh: bool = False) -> LoginSettings:
        cached = self._cached
        if not fresh and cached and time.monotonic() - cached[0] < self.ttl_s:
            return cached[1]
        try:
            value = await self.repo.get()
        except Exception:  # база моргнула — живём на последнем известном, вход из-за этого не роняем
            log.warning("login settings read failed", exc_info=True)
            if cached:
                return cached[1]
            raise
        if value is None:
            value = await self.repo.ensure(initial_settings(self.settings))
        self._remember(value)
        return value

    async def save(self, value: LoginSettings, by: str) -> LoginSettings:
        saved = await self.repo.save(value, by)
        self._remember(saved)
        return saved

    def active(self, value: LoginSettings, method: str) -> bool:
        """Способ включён администратором и настроен на сервере (доступность IdP не учитывается)."""
        return self.configured(method) and value.enabled(method)

    def active_methods(self, value: LoginSettings) -> list[str]:
        return [m for m in METHODS if self.active(value, m)]

    def password_allowed(self, value: LoginSettings, roles: list[str]) -> bool:
        return value.password_enabled or (self.break_glass and "admin" in roles)

    async def method_on(self, method: str) -> bool:
        return self.active(await self.current(), method)

    async def session_allowed(self, auth_method: str, roles: list[str]) -> bool:
        """Сессия, выданная способом, который потом выключили, больше не действует."""
        if auth_method not in METHODS:
            return True
        value = await self.current()
        if auth_method == "password":
            return self.password_allowed(value, roles)
        return self.active(value, auth_method)


def diff(before: LoginSettings, after: LoginSettings) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for f in fields(LoginSettings):
        if f.name not in EDITABLE:
            continue
        old, new = getattr(before, f.name), getattr(after, f.name)
        if old != new:
            changes[f.name] = {"from": old, "to": new}
    return changes
