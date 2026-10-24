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


def make_require_admin(
    current_user: Callable[[Request], User], *, message: str = FORBIDDEN_MESSAGE
) -> Callable[[User], User]:
    def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
        if not user.is_admin:
            raise ApiError(403, "forbidden", message)
        return user

    return require_admin
