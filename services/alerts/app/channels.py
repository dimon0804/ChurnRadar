"""Каналы доставки: webhook, Telegram Bot API, почта. Одна попытка на вызов, повторы — в delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import smtplib
from typing import Any, Protocol

import httpx
from svckit.mail import Mail, MailConfig, MailDisabled, send_mail

from .config import Config


class DeliveryError(Exception):
    """Попытка не удалась. retryable=False — повтор не поможет (неверный адрес, чат, токен)."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class Channel(Protocol):
    name: str

    async def send(self, payload: dict[str, Any], *, batch_id: str, recipient: str | None = None) -> None: ...


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

    async def send(self, payload: dict[str, Any], *, batch_id: str, recipient: str | None = None) -> None:
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


class TelegramApi:
    """Минимальный клиент Bot API: sendMessage, getMe, getUpdates. Токен в URL, поэтому в тексты
    ошибок попадает только тип исключения."""

    def __init__(self, client: httpx.AsyncClient, token: str, *, api_url: str, timeout_s: float = 10.0) -> None:
        self._client = client
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout_s

    async def call(self, method: str, body: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        url = f"{self._api_url}/bot{self._token}/{method}"
        try:
            response = await self._client.post(url, json=body or {}, timeout=timeout or self._timeout)
        except httpx.HTTPError as exc:
            raise DeliveryError(f"Telegram недоступен: {type(exc).__name__}") from exc
        _check(response, "Telegram")
        data = response.json()
        if not data.get("ok"):
            raise DeliveryError(f"Telegram: {data.get('description', 'ошибка')}", retryable=False)
        return data.get("result")

    async def send_message(
        self, chat_id: str | int, text: str, *, parse_mode: str | None = None, button: dict[str, str] | None = None
    ) -> None:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4000],  # лимит Telegram — 4096 символов
            "disable_web_page_preview": True,
        }
        if parse_mode:
            body["parse_mode"] = parse_mode
        if button:
            body["reply_markup"] = {"inline_keyboard": [[{"text": button["text"], "url": button["url"]}]]}
        await self.call("sendMessage", body)


class TelegramChannel:
    """Личный чат пользователя (recipient = chat id) или общий чат команды (TELEGRAM_CHAT_ID)."""

    name = "telegram"

    def __init__(self, api: TelegramApi, team_chat_id: str = "") -> None:
        self._api = api
        self._team_chat_id = team_chat_id

    async def send(self, payload: dict[str, Any], *, batch_id: str, recipient: str | None = None) -> None:
        chat_id = recipient or self._team_chat_id
        if not chat_id:
            raise DeliveryError("нет чата для отправки", retryable=False)
        await self._api.send_message(
            chat_id, str(payload.get("text", "")), parse_mode=payload.get("parse_mode"), button=payload.get("button")
        )


class EmailChannel:
    name = "email"

    def __init__(self, config: MailConfig) -> None:
        self._config = config

    async def send(self, payload: dict[str, Any], *, batch_id: str, recipient: str | None = None) -> None:
        if not recipient:
            raise DeliveryError("не указан адрес", retryable=False)
        mail = Mail(
            to=recipient,
            subject=str(payload.get("subject", "ChurnRadar")),
            text=str(payload.get("text", "")),
            html=payload.get("html"),
            headers={"X-ChurnRadar-Batch-Id": batch_id},
        )
        try:
            await send_mail(self._config, mail)
        except MailDisabled as exc:
            raise DeliveryError("почта не настроена (SMTP_HOST)", retryable=False) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise DeliveryError(f"адрес не принят: {recipient}", retryable=False) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryError(f"SMTP: {type(exc).__name__}: {exc}") from exc


def build_channels(config: Config, client: httpx.AsyncClient) -> dict[str, Channel]:
    channels: dict[str, Channel] = {}
    if config.webhook_enabled:
        channels["webhook"] = WebhookChannel(
            client, config.webhook_url, secret=config.webhook_secret, timeout_s=config.webhook_timeout_s
        )
    if config.telegram_bot_token:
        api = TelegramApi(
            client, config.telegram_bot_token, api_url=config.telegram_api_url, timeout_s=config.telegram_timeout_s
        )
        channels["telegram"] = TelegramChannel(api, config.telegram_chat_id)
    if config.mail.enabled:
        channels["email"] = EmailChannel(config.mail)
    return channels
