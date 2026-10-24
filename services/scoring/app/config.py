from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Config:
    database_url: str
    nats_url: str
    log_level: str
    http_host: str
    http_port: int
    metrics_token: str
    rescore_interval_s: float
    debounce_ms: int
    fetch_batch: int
    incremental_max_accounts: int
    stream_name: str
    durable_name: str
    ack_wait_s: float
    cache_results: bool
    advisory_lock_key: int
    chunk_size: int
    workers: int

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            database_url=os.getenv("DATABASE_URL", "postgresql://churn:churn@postgres:5432/churn"),
            nats_url=os.getenv("NATS_URL", "nats://nats:4222"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            http_host=os.getenv("HTTP_HOST", "0.0.0.0"),
            http_port=int(os.getenv("HTTP_PORT", "8000")),
            metrics_token=os.getenv("METRICS_TOKEN", "churn-metrics-token"),
            rescore_interval_s=float(os.getenv("RESCORE_INTERVAL_S", "60")),
            debounce_ms=int(os.getenv("DEBOUNCE_MS", "300")),
            fetch_batch=int(os.getenv("FETCH_BATCH", "256")),
            incremental_max_accounts=int(os.getenv("INCREMENTAL_MAX_ACCOUNTS", "5000")),
            stream_name=os.getenv("NATS_STREAM", "CHURN"),
            durable_name=os.getenv("NATS_DURABLE", "scoring"),
            ack_wait_s=float(os.getenv("ACK_WAIT_S", "300")),
            cache_results=_env_bool("CACHE_RESULTS", True),
            advisory_lock_key=int(os.getenv("ADVISORY_LOCK_KEY", "7340001")),
            chunk_size=int(os.getenv("RESCORE_CHUNK_SIZE", "5000")),
            workers=int(os.getenv("SCORING_WORKERS", str(min(4, os.cpu_count() or 1)))),
        )
