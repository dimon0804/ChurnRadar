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


settings = Settings()
