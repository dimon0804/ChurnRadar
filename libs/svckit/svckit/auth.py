"""Пользователь из заголовков гейтвея (Traefik ForwardAuth проставляет X-User-*).

Без `from __future__ import annotations`: FastAPI должен видеть Depends(current_user)
из замыкания как объект, а не строку.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import Depends, Request

from .errors import ApiError

UNAUTHORIZED_MESSAGE = "Требуется вход в систему"
FORBIDDEN_MESSAGE = "Недостаточно прав: действие доступно только администратору"

# Роли вложены: маркетолог может всё, что аналитик, администратор — всё, что маркетолог.
ROLE_RANK = {"analyst": 1, "marketer": 2, "admin": 3}
ROLE_FORBIDDEN_MESSAGES = {
    "admin": FORBIDDEN_MESSAGE,
    "marketer": "Недостаточно прав: действие доступно маркетологу и администратору",
    "analyst": "Недостаточно прав для этого действия",
}


class AuthSettings(Protocol):
    """Откуда брать настройки; читаются на каждом запросе, поэтому их можно менять на лету (в тестах)."""

    @property
    def auth_trust_headers(self) -> bool: ...

    @property
    def auth_dev_user(self) -> str | None: ...

    @property
    def auth_dev_roles(self) -> str: ...


@dataclass(frozen=True, slots=True)
class User:
    id: str | None
    name: str
    roles: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def rank(self) -> int:
        return max((ROLE_RANK.get(r, 0) for r in self.roles), default=0)

    def has_role(self, minimum: str) -> bool:
        return self.rank >= ROLE_RANK[minimum]


def parse_roles(raw: str | None, *, lower: bool = True) -> frozenset[str]:
    if not raw:
        return frozenset()
    roles = (r.strip() for r in raw.split(","))
    return frozenset((r.lower() if lower else r) for r in roles if r)


def make_current_user(
    settings: AuthSettings,
    *,
    lower_roles: bool = True,
    unauthorized_message: str = UNAUTHORIZED_MESSAGE,
) -> Callable[[Request], User]:
    """Зависимость FastAPI: пользователь из X-User-*, иначе AUTH_DEV_USER, иначе 401."""

    def current_user(request: Request) -> User:
        name = request.headers.get("X-User-Name") if settings.auth_trust_headers else None
        if name:
            return User(
                id=request.headers.get("X-User-Id"),
                name=name,
                roles=parse_roles(request.headers.get("X-User-Roles"), lower=lower_roles),
            )
        if settings.auth_dev_user:
            return User(
                id="dev",
                name=settings.auth_dev_user,
                roles=parse_roles(settings.auth_dev_roles, lower=lower_roles),
            )
        raise ApiError(401, "unauthorized", unauthorized_message)

    return current_user


def make_require_role(
    current_user: Callable[[Request], User], minimum: str, *, message: str | None = None
) -> Callable[[User], User]:
    """Зависимость: пользователь с ролью не ниже `minimum` (analyst < marketer < admin), иначе 403."""
    if minimum not in ROLE_RANK:
        raise ValueError(f"unknown role {minimum!r}")
    text = message or ROLE_FORBIDDEN_MESSAGES[minimum]

    def require_role(user: Annotated[User, Depends(current_user)]) -> User:
        if not user.has_role(minimum):
            raise ApiError(403, "forbidden", text)
        return user

    return require_role


def make_require_admin(
    current_user: Callable[[Request], User], *, message: str = FORBIDDEN_MESSAGE
) -> Callable[[User], User]:
    return make_require_role(current_user, "admin", message=message)
