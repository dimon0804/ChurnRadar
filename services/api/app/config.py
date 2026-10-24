"""Настройки api из переменных окружения."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_openapi_path() -> str:
    """В образе спецификация лежит в /app/contracts, при запуске из репозитория — в его корне."""
    here = Path(__file__).resolve()
    candidates = [Path("/app/contracts/openapi.yaml")]
    candidates += [parent / "contracts" / "openapi.yaml" for parent in here.parents]
    for path in candidates:
        if path.is_file():
            return str(path)
    return str(candidates[0])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    database_url: str = "postgresql://churn:churn@postgres:5432/churn"
    nats_url: str = "nats://nats:4222"
    redis_url: str = "redis://redis:6379/0"
    log_level: str = "INFO"

    auth_trust_headers: bool = True
    auth_dev_user: str | None = None
    auth_dev_roles: str = "admin"

    metrics_token: str = "churn-metrics-token"
    simulator_url: str = "http://simulator:8003"
    simulator_timeout_s: float = 120.0
    openapi_path: str = _default_openapi_path()
    summary_cache_ttl_s: float = 2.0

    db_pool_min: int = 1
    db_pool_max: int = 10
    nats_wait_on_start_s: float = 5.0


settings = Settings()
