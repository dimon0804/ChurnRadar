"""Роли панели из того, что прислал внешний IdP (OIDC claims или атрибуты SAML)."""

from __future__ import annotations

from collections.abc import Iterable

from .config import Settings
from .users import VALID_ROLES


def _split(value: str) -> set[str]:
    return {_norm(part) for part in value.split(",") if part.strip()}


def _norm(group: str) -> str:
    # Keycloak отдаёт группы путями (/churn-admins), Authentik и Entra — просто именами
    return group.strip().strip("/").rsplit("/", 1)[-1].lower()


def map_roles(settings: Settings, roles: Iterable[str] = (), groups: Iterable[str] = ()) -> list[str]:
    """Явные роли (admin/marketer/analyst) плюс роли из групп. Пустой список — группы роли не дали.

    Роль по умолчанию сюда не подмешивается: её задаёт администратор в настройках входа,
    и применяет её sso_sign_in.
    """
    found = {str(r).strip().lower() for r in roles} & set(VALID_ROLES)
    names = {_norm(str(g)) for g in groups if str(g).strip()}
    if names & _split(settings.sso_admin_groups):
        found.add("admin")
    if names & _split(settings.sso_marketer_groups):
        found.add("marketer")
    if names & _split(settings.sso_analyst_groups):
        found.add("analyst")
    return [r for r in VALID_ROLES if r in found]
