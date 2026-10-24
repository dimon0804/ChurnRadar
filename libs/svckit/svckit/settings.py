"""Общие настройки HTTP-сервисов из переменных окружения."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    database_url: str = "postgresql://churn:churn@postgres:5432/churn"
    nats_url: str = "nats://nats:4222"
    log_level: str = "INFO"

    auth_trust_headers: bool = True
    auth_dev_user: str | None = None
    auth_dev_roles: str = "admin"

    metrics_token: str = "churn-metrics-token"

    db_pool_min: int = 1
    db_pool_max: int = 5
    nats_wait_on_start_s: float = 15.0
