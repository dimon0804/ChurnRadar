from __future__ import annotations

import os
from dataclasses import dataclass

from .events import SUBJECT_DLQ


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_floats(name: str, default: str) -> tuple[float, ...]:
    raw = os.getenv(name, default)
    return tuple(float(x) for x in raw.split(",") if x.strip())


def _env_str(name: str) -> str:
    return (os.getenv(name) or "").strip()


@dataclass(frozen=True, slots=True)
class Config:
    database_url: str = "postgresql://churn:churn@postgres:5432/churn"
    nats_url: str = "nats://nats:4222"
    log_level: str = "INFO"
    http_host: str = "0.0.0.0"
    http_port: int = 8000
    metrics_token: str = "churn-metrics-token"

    # пользователь из X-User-* (ForwardAuth в Traefik), как у api
    auth_trust_headers: bool = True
    auth_dev_user: str | None = None
    auth_dev_roles: str = "admin"

    stream_name: str = "CHURN"
    durable_name: str = "alerts"
    ack_wait_s: float = 30.0
    fetch_batch: int = 32
    max_deliver: int = 10
    retry_backoff_s: tuple[float, ...] = (1.0, 5.0, 30.0, 120.0)
    dlq_subject: str = SUBJECT_DLQ
    publish_attempts: int = 3
    shutdown_grace_s: float = 20.0

    # пороги
    loss_jump_cents: int = 5_000  # ALERT_LOSS_JUMP_EUR, по умолчанию €50
    total_loss_jump_pct: float = 10.0
    cooldown_s: float = 3600.0  # ALERT_COOLDOWN_MIN
    max_items_in_message: int = 10
    # больше стольких оповещений по клиентам за пересчёт — одна сводная запись вместо всех
    bulk_threshold: int = 20

    # доставка
    public_url: str = "http://localhost:8080"
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_timeout_s: float = 5.0
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_api_url: str = "https://api.telegram.org"
    telegram_timeout_s: float = 10.0
    delivery_interval_s: float = 2.0
    delivery_backoff_s: tuple[float, ...] = (5.0, 15.0, 60.0, 300.0, 900.0)
    delivery_max_attempts: int = 8
    # сколько держим взятую доставку за собой; упавшая реплика отдаст её другой через lease
    delivery_lease_s: float = 60.0
    feed_retention_days: int = 30

    @property
    def webhook_enabled(self) -> bool:
        return bool(self.webhook_url)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def enabled_channels(self) -> list[str]:
        channels = []
        if self.webhook_enabled:
            channels.append("webhook")
        if self.telegram_enabled:
            channels.append("telegram")
        return channels

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            database_url=os.getenv("DATABASE_URL", "postgresql://churn:churn@postgres:5432/churn"),
            nats_url=os.getenv("NATS_URL", "nats://nats:4222"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            http_host=os.getenv("HTTP_HOST", "0.0.0.0"),
            http_port=int(os.getenv("HTTP_PORT", "8000")),
            metrics_token=os.getenv("METRICS_TOKEN", "churn-metrics-token"),
            auth_trust_headers=_env_bool("AUTH_TRUST_HEADERS", True),
            auth_dev_user=os.getenv("AUTH_DEV_USER") or None,
            auth_dev_roles=os.getenv("AUTH_DEV_ROLES", "admin"),
            stream_name=os.getenv("NATS_STREAM", "CHURN"),
            durable_name=os.getenv("NATS_DURABLE", "alerts"),
            ack_wait_s=float(os.getenv("ACK_WAIT_S", "30")),
            fetch_batch=max(1, int(os.getenv("FETCH_BATCH", "32"))),
            max_deliver=max(1, int(os.getenv("MAX_DELIVER", "10"))),
            retry_backoff_s=_env_floats("RETRY_BACKOFF_S", "1,5,30,120") or (1.0,),
            dlq_subject=os.getenv("DLQ_SUBJECT", SUBJECT_DLQ),
            publish_attempts=max(1, int(os.getenv("PUBLISH_ATTEMPTS", "3"))),
            shutdown_grace_s=float(os.getenv("SHUTDOWN_GRACE_S", "20")),
            loss_jump_cents=round(float(os.getenv("ALERT_LOSS_JUMP_EUR", "50")) * 100),
            total_loss_jump_pct=float(os.getenv("ALERT_TOTAL_LOSS_JUMP_PCT", "10")),
            cooldown_s=float(os.getenv("ALERT_COOLDOWN_MIN", "60")) * 60,
            max_items_in_message=max(1, int(os.getenv("ALERT_MAX_ITEMS_IN_MESSAGE", "10"))),
            bulk_threshold=max(0, int(os.getenv("ALERTS_BULK_THRESHOLD", "20"))),
            public_url=(os.getenv("PUBLIC_URL") or "http://localhost:8080").rstrip("/"),
            webhook_url=_env_str("WEBHOOK_URL"),
            webhook_secret=_env_str("WEBHOOK_SECRET"),
            webhook_timeout_s=float(os.getenv("WEBHOOK_TIMEOUT_S", "5")),
            telegram_bot_token=_env_str("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env_str("TELEGRAM_CHAT_ID"),
            telegram_api_url=(os.getenv("TELEGRAM_API_URL") or "https://api.telegram.org").rstrip("/"),
            telegram_timeout_s=float(os.getenv("TELEGRAM_TIMEOUT_S", "10")),
            delivery_interval_s=float(os.getenv("DELIVERY_INTERVAL_S", "2")),
            delivery_backoff_s=_env_floats("DELIVERY_BACKOFF_S", "5,15,60,300,900") or (5.0,),
            delivery_max_attempts=max(1, int(os.getenv("DELIVERY_MAX_ATTEMPTS", "8"))),
            delivery_lease_s=float(os.getenv("DELIVERY_LEASE_S", "60")),
            feed_retention_days=max(1, int(os.getenv("FEED_RETENTION_DAYS", "30"))),
        )
