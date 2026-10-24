"""Настройки ingest из переменных окружения."""

from __future__ import annotations

from datetime import datetime

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    database_url: str = "postgresql://churn:churn@postgres:5432/churn"
    nats_url: str = "nats://nats:4222"
    log_level: str = "INFO"

    auth_trust_headers: bool = True
    auth_dev_user: str | None = None
    auth_dev_roles: str = "admin"

    metrics_token: str = "churn-metrics-token"

    seed_on_start: bool = True
    seed_path: str = "/app/data/sample/data.json"
    seed_reference_time: datetime | None = datetime.fromisoformat("2026-10-19T15:00:00+00:00")

    max_body_bytes: int = 50 * 1024 * 1024
    db_pool_min: int = 1
    db_pool_max: int = 5
    nats_wait_on_start_s: float = 15.0


settings = Settings()
