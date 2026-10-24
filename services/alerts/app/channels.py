"""Каналы доставки: webhook (JSON POST) и Telegram Bot API. Одна попытка на вызов, повторы — в delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Protocol

import httpx

from .config import Config


class DeliveryError(Exception):
    """Попытка не удалась. retryable=False — повтор не поможет (неверный адрес, чат, токен)."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class Channel(Protocol):
    name: str

    async def send(self, payload: dict[str, Any], *, batch_id: str) -> None: ...


def _check(response: httpx.Response, what: str) -> None:
    if response.is_success:
        return
    # 408 и 429 — временные, как и 5xx; остальные 4xx — ошибка настройки
    retryable = response.status_code >= 500 or response.status_code in (408, 429)
    raise DeliveryError(f"{what} ответил {response.status_code}: {response.text[:200]}", retryable=retryable)


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookChannel:
    name = "webhook"

    def __init__(self, client: httpx.AsyncClient, url: str, *, secret: str = "", timeout_s: float = 5.0) -> None:
        self._client = client
        self._url = url
        self._secret = secret
        self._timeout = timeout_s

    async def send(self, payload: dict[str, Any], *, batch_id: str) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "ChurnRadar-Alerts/1.0",
            # получатель может отсечь повтор той же пачки
            "X-ChurnRadar-Batch-Id": batch_id,
        }
        if self._secret:
            headers["X-ChurnRadar-Signature"] = sign(self._secret, body)
        try:
            response = await self._client.post(self._url, content=body, headers=headers, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise DeliveryError(f"webhook недоступен: {type(exc).__name__}: {exc}") from exc
        _check(response, "webhook")


class TelegramChannel:
    name = "telegram"

    def __init__(
        self, client: httpx.AsyncClient, token: str, chat_id: str, *, api_url: str, timeout_s: float = 10.0
    ) -> None:
        self._client = client
        self._token = token
        self._chat_id = chat_id
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout_s

    async def send(self, payload: dict[str, Any], *, batch_id: str) -> None:
        url = f"{self._api_url}/bot{self._token}/sendMessage"
        body = {
            "chat_id": self._chat_id,
            "text": str(payload.get("text", ""))[:4000],  # лимит Telegram — 4096 символов
            "disable_web_page_preview": True,
        }
        try:
            response = await self._client.post(url, json=body, timeout=self._timeout)
        except httpx.HTTPError as exc:
            # в тексте исключения httpx может оказаться URL с токеном
            raise DeliveryError(f"Telegram недоступен: {type(exc).__name__}") from exc
        _check(response, "Telegram")


def build_channels(config: Config, client: httpx.AsyncClient) -> dict[str, Channel]:
    channels: dict[str, Channel] = {}
    if config.webhook_enabled:
        channels["webhook"] = WebhookChannel(
            client, config.webhook_url, secret=config.webhook_secret, timeout_s=config.webhook_timeout_s
        )
    if config.telegram_enabled:
        channels["telegram"] = TelegramChannel(
            client,
            config.telegram_bot_token,
            config.telegram_chat_id,
            api_url=config.telegram_api_url,
            timeout_s=config.telegram_timeout_s,
        )
    return channels
