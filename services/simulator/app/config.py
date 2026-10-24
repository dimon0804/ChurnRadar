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
        )
