from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


MODES = ("push", "pull")


def _mode(raw: str) -> str:
    mode = raw.strip().lower()
    if mode not in MODES:
        raise ValueError(f"SIMULATOR_MODE: ожидается push или pull, получено {raw!r}")
    return mode


@dataclass(frozen=True, slots=True)
class Config:
    database_url: str
    ingest_url: str
    log_level: str
    http_host: str
    http_port: int
    metrics_token: str
    interval_ms: int
    running: bool
    demo_day_seconds: float
    max_changes: int
    auth_trust_headers: bool
    auth_dev_user: str | None
    auth_dev_roles: str
    snapshot_reference: str
    ingest_timeout_s: float
    snapshot_gzip_level: int
    events_timeout_s: float
    events_retries: int
    events_retry_backoff_s: float
    mode: str
    demo_billing_token: str
    demo_metrica_token: str
    demo_metrica_counter: int
    demo_metrica_processing_s: float
    demo_metrica_max_accounts: int

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            database_url=os.getenv("DATABASE_URL", "postgresql://churn:churn@postgres:5432/churn"),
            ingest_url=os.getenv("INGEST_URL", "http://ingest:8000").rstrip("/"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            http_host=os.getenv("HTTP_HOST", "0.0.0.0"),
            http_port=int(os.getenv("HTTP_PORT", "8000")),
            metrics_token=os.getenv("METRICS_TOKEN", "churn-metrics-token"),
            interval_ms=int(os.getenv("SIMULATOR_INTERVAL_MS", "2000")),
            running=_env_bool("SIMULATOR_RUNNING", False),
            demo_day_seconds=float(os.getenv("DEMO_DAY_SECONDS", "24")),
            max_changes=int(os.getenv("SIMULATOR_MAX_CHANGES", "5")),
            auth_trust_headers=_env_bool("AUTH_TRUST_HEADERS", True),
            auth_dev_user=os.getenv("AUTH_DEV_USER") or None,
            auth_dev_roles=os.getenv("AUTH_DEV_ROLES", "admin"),
            # now — опорное время снимка = момент генерации; null — время идёт (as_of = now()).
            snapshot_reference=os.getenv("SNAPSHOT_REFERENCE", "now").strip().lower(),
            ingest_timeout_s=float(os.getenv("INGEST_TIMEOUT_S", "600")),
            snapshot_gzip_level=int(os.getenv("SNAPSHOT_GZIP_LEVEL", "5")),
            # пакет изменений несёт абсолютные значения строк, ingest делает upsert —
            # повторная отправка того же пакета безопасна
            events_timeout_s=float(os.getenv("INGEST_EVENTS_TIMEOUT_S", "30")),
            events_retries=max(0, int(os.getenv("INGEST_EVENTS_RETRIES", "2"))),
            events_retry_backoff_s=float(os.getenv("INGEST_EVENTS_RETRY_BACKOFF_S", "0.5")),
            # push — сам шлёт изменения в ingest; pull — только отдаёт их демо-API биллинга,
            # а забирает коннектор ingest
            mode=_mode(os.getenv("SIMULATOR_MODE", "push")),
            # токены демо-источников; пусто — без проверки (в compose заданы)
            demo_billing_token=os.getenv("DEMO_BILLING_TOKEN", ""),
            demo_metrica_token=os.getenv("DEMO_METRICA_TOKEN", ""),
            demo_metrica_counter=int(os.getenv("DEMO_METRICA_COUNTER", "100500")),
            demo_metrica_processing_s=float(os.getenv("DEMO_METRICA_PROCESSING_S", "1")),
            # визиты генерируем первым N клиентам по id: на 200k клиентов выгрузка была бы гигабайтной
            demo_metrica_max_accounts=int(os.getenv("DEMO_METRICA_MAX_ACCOUNTS", "20000")),
        )
