"""Пользователь из заголовков гейтвея (Traefik ForwardAuth проставляет X-User-*)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.config import settings
from app.errors import ApiError


@dataclass(frozen=True, slots=True)
class User:
    id: str | None
    name: str
    roles: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles


def _parse_roles(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    return frozenset(r.strip().lower() for r in raw.split(",") if r.strip())


def current_user(request: Request) -> User:
    name = request.headers.get("X-User-Name") if settings.auth_trust_headers else None
    if name:
        return User(
            id=request.headers.get("X-User-Id"),
            name=name,
            roles=_parse_roles(request.headers.get("X-User-Roles")),
        )
    if settings.auth_dev_user:
        return User(id="dev", name=settings.auth_dev_user, roles=_parse_roles(settings.auth_dev_roles))
    raise ApiError(401, "unauthorized", "Требуется вход в систему")


def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_admin:
        raise ApiError(403, "forbidden", "Недостаточно прав: действие доступно только администратору")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
