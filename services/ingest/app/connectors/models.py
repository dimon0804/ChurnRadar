"""Схемы API коннекторов и проверка опций по типу источника (contracts/openapi.yaml, тег connectors)."""

from __future__ import annotations

import os
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Kind = Literal["billing_http", "yandex_metrica"]
ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,40}$"
TOKEN_ENV_PATTERN = r"^CONNECTOR_TOKEN_[A-Z0-9_]{1,64}$"

KIND_TITLES = {"billing_http": "HTTP API биллинга", "yandex_metrica": "Яндекс Метрика"}

# Страницы сайта хостинга, по которым считаем визиты. Шаблон — подстрока адреса без учёта регистра.
DEFAULT_PAGES: dict[str, list[str]] = {
    "payment": ["/billing", "/balance", "/payment", "/pay/"],
    "cancel": ["/cancel", "/delete", "/remove", "/unsubscribe"],
    "pricing": ["/pricing", "/tariffs", "/prices"],
}
PAGE_GROUPS = tuple(DEFAULT_PAGES)


class BillingOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_size: int = Field(default=500, ge=1, le=1000)
    max_records: int = Field(default=50_000, ge=1, le=1_000_000)


class MetricaOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    counter_id: int | None = Field(default=None, ge=1)
    lookback_days: int = Field(default=35, ge=1, le=90)
    refresh_days: int = Field(default=3, ge=0, le=10)
    account_param: str = Field(default="account_id", min_length=1, max_length=100)
    tz_offset_hours: float = Field(default=3.0, ge=-12, le=14)
    pages: dict[str, list[str]] = Field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_PAGES.items()})
    goals: dict[str, list[int]] = Field(default_factory=dict)
    poll_interval_s: float = Field(default=5.0, ge=0.05, le=120)
    max_wait_s: float = Field(default=600.0, ge=1, le=3600)

    @field_validator("pages", "goals")
    @classmethod
    def _known_groups(cls, value: dict[str, Any]) -> dict[str, Any]:
        unknown = set(value) - set(PAGE_GROUPS)
        if unknown:
            raise ValueError(f"неизвестные группы страниц: {', '.join(sorted(unknown))}; есть {', '.join(PAGE_GROUPS)}")
        return value


OPTION_MODELS: dict[str, type[BaseModel]] = {"billing_http": BillingOptions, "yandex_metrica": MetricaOptions}


def validate_options(kind: str, options: dict[str, Any]) -> dict[str, Any]:
    """Опции с значениями по умолчанию; ValueError с понятным текстом, если что-то не так."""
    try:
        return OPTION_MODELS[kind].model_validate(options).model_dump()
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'options'}: {e['msg']}" for e in exc.errors())
        raise ValueError(problems) from exc


def allowed_hosts(raw: str) -> frozenset[str]:
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def check_base_url(url: str, allowed: frozenset[str]) -> str | None:
    """Текст ошибки или None. Разрешаем http и https; при списке хостов — только их."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "адрес источника должен начинаться с http:// или https://"
    if parts.username or parts.password:
        return "логин и пароль в адресе не храним: токен задаётся переменной окружения"
    if allowed and parts.hostname.lower() not in allowed:
        return f"хост {parts.hostname} не входит в CONNECTOR_ALLOWED_HOSTS"
    return None


def token_present(token_env: str | None) -> bool:
    return bool(token_env and os.environ.get(token_env, "").strip())


def read_token(token_env: str | None) -> str | None:
    if not token_env:
        return None
    return os.environ.get(token_env, "").strip() or None


class ConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    kind: Kind
    title: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    interval_s: int = Field(default=300, ge=10, le=604_800)
    enabled: bool = False
    token_env: str | None = Field(default=None, pattern=TOKEN_ENV_PATTERN)
    options: dict[str, Any] = Field(default_factory=dict)


class ConnectorPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    interval_s: int | None = Field(default=None, ge=10, le=604_800)
    enabled: bool | None = None
    token_env: str | None = Field(default=None, pattern=TOKEN_ENV_PATTERN)
    options: dict[str, Any] | None = None


class ClientIdItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    client_id: str = Field(pattern=r"^[0-9]{1,20}$")
    account_id: int = Field(ge=1, le=9_223_372_036_854_775_807)


class ClientIdsUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replace: bool = False
    items: list[ClientIdItem] = Field(max_length=200_000)
