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
    # таймауты сессии Postgres, мс (0 — без лимита)
    db_statement_timeout_ms: int = 60_000
    db_idle_in_transaction_timeout_ms: int = 60_000
    nats_wait_on_start_s: float = 15.0

    # relay outbox: пачка за проход, проверка по таймеру (кроме пинка после записи), хранение отправленных
    outbox_batch_size: int = 100
    outbox_interval_s: float = 5.0
    outbox_backoff_max_s: float = 30.0
    outbox_retention_s: float = 24 * 3600
