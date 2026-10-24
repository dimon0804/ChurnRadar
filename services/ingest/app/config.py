"""Настройки ingest из переменных окружения."""

from __future__ import annotations

from datetime import datetime

from svckit.settings import ServiceSettings

MB = 1024 * 1024


class Settings(ServiceSettings):
    seed_on_start: bool = True
    seed_path: str = "/app/data/sample/data.json"
    seed_reference_time: datetime | None = datetime.fromisoformat("2026-10-19T15:00:00+00:00")

    # Тело запроса после распаковки (снимок 200k аккаунтов — около 390 МБ JSON).
    max_body_bytes: int = 512 * MB
    # Сжатое тело (Content-Encoding: gzip) на входе; JSON снимка сжимается примерно в 9 раз.
    max_compressed_body_bytes: int = 128 * MB

    # Коннекторы (pull): планировщик в каждой реплике, исполняет только лидер.
    connectors_enabled: bool = True
    connectors_tick_s: float = 2.0
    # потолок одного запуска; Метрика готовит выгрузку минутами, поэтому с запасом
    connector_run_timeout_s: float = 900.0
    connector_http_timeout_s: float = 30.0
    connector_http_attempts: int = 3
    connector_retry_base_s: float = 1.0
    # пауза после неудачного запуска: base · 2^(n−1), но не дольше интервала коннектора
    connector_failure_backoff_s: float = 30.0
    # если задано — коннектор может ходить только на эти хосты (через запятую)
    connector_allowed_hosts: str = ""
    # коннекторы из окружения (managed): адрес, токен и счётчик задаются здесь, а не в интерфейсе
    connector_billing_url: str = ""
    connector_billing_interval_s: int = 60
    connector_metrica_url: str = ""
    connector_metrica_counter: int | None = None
    connector_metrica_interval_s: int = 3600


settings = Settings()
