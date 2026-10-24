"""svckit: общий код HTTP-сервисов ChurnRadar.

Модули с внешними зависимостями (db — asyncpg, bus — nats-py, settings — pydantic-settings)
импортируются явно, чтобы сервису не приходилось ставить то, чем он не пользуется.
"""

from .auth import ROLE_RANK, User, make_current_user, make_require_admin, make_require_role, parse_roles
from .errors import ApiError, RequestIdMiddleware, error_response, install_error_handlers

__all__ = [
    "ROLE_RANK",
    "ApiError",
    "RequestIdMiddleware",
    "User",
    "error_response",
    "install_error_handlers",
    "make_current_user",
    "make_require_admin",
    "make_require_role",
    "parse_roles",
]
