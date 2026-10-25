"""Кто что может менять: правила для ForwardAuth на шлюзе.

Роли вложены: аналитик < маркетолог < администратор. Читать (GET/HEAD) может любой вошедший,
а для изменяющих запросов действует таблица ниже: первое совпавшее правило задаёт минимальную
роль. Сервисы проверяют права ещё раз сами (svckit.auth.make_require_role), шлюз — первая линия.
"""

from __future__ import annotations

from collections.abc import Iterable

ROLE_RANK = {"analyst": 1, "marketer": 2, "admin": 3}
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# (префикс пути, минимальная роль) — от частного к общему
UNSAFE_RULES: tuple[tuple[str, str], ...] = (
    # настройка системы — только администратор
    ("/api/v1/model", "admin"),
    ("/api/v1/source", "admin"),
    ("/api/v1/simulator", "admin"),
    ("/api/v1/mail/templates", "admin"),
    ("/api/v1/mail/settings", "admin"),
    ("/api/v1/analytics/marketing/settings", "admin"),
    ("/api/v1/alerts/preferences/users", "admin"),
    ("/api/v1/auth/users", "admin"),
    # способы входа и регистрация через SSO
    ("/api/v1/auth/login-settings", "admin"),
    # API-токены интеграций, расписание и рассылка отчёта
    ("/api/v1/auth/tokens", "admin"),
    ("/api/v1/reports", "admin"),
    # предпросмотр шаблона на клиенте ничего не меняет — смотреть может любой вошедший
    ("/api/v1/mail/preview", "analyst"),
    # работа с клиентами: письма, рассылки, отметки «связались/решено/отложить»
    ("/api/v1/mail", "marketer"),
    ("/api/v1/customers", "marketer"),
    # сценарии удержания: автоматические письма по правилам — тоже работа маркетолога
    ("/api/v1/playbooks", "marketer"),
    # своё: уведомления, профиль, выход — любой вошедший
    ("/api/v1/alerts", "analyst"),
    ("/api/v1/auth", "analyst"),
)
# изменяющий запрос, под который не подошло ни одно правило, — только администратору
DEFAULT_UNSAFE_ROLE = "admin"

ROLE_FORBIDDEN_MESSAGES = {
    "admin": "Это действие доступно только администратору",
    "marketer": "Это действие доступно маркетологу и администратору",
    "analyst": "Недостаточно прав для этого действия",
}


def _matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def required_role(method: str, path: str) -> str | None:
    """Минимальная роль для запроса; None — достаточно быть вошедшим."""
    if method.upper() not in UNSAFE_METHODS:
        return None
    for prefix, role in UNSAFE_RULES:
        if _matches(path, prefix):
            return role
    return DEFAULT_UNSAFE_ROLE


def rank(roles: Iterable[str]) -> int:
    return max((ROLE_RANK.get(r, 0) for r in roles), default=0)


def allowed(roles: Iterable[str], method: str, path: str) -> tuple[bool, str | None]:
    need = required_role(method, path)
    if need is None:
        return True, None
    return rank(roles) >= ROLE_RANK[need], need
