"""Отправка писем по SMTP: общий код для писем клиентам и уведомлений сотрудникам.

Стандартный smtplib в отдельном потоке, чтобы не держать цикл событий. Настройки берутся
из переменных окружения SMTP_*; без SMTP_HOST отправка выключена, а не падает.
"""

from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid


class MailDisabled(RuntimeError):
    """SMTP не настроен: письмо отправить некуда."""


@dataclass(frozen=True, slots=True)
class MailConfig:
    host: str = ""
    port: int = 25
    username: str = ""
    password: str = ""
    # none | starttls | tls
    security: str = "none"
    from_addr: str = "churnradar@example.test"
    from_name: str = "ChurnRadar"
    timeout_s: float = 15.0

    @property
    def enabled(self) -> bool:
        return bool(self.host)

    @classmethod
    def from_env(cls) -> MailConfig:
        return cls(
            host=os.getenv("SMTP_HOST", ""),
            port=int(os.getenv("SMTP_PORT", "25")),
            username=os.getenv("SMTP_USERNAME", ""),
            password=os.getenv("SMTP_PASSWORD", ""),
            security=os.getenv("SMTP_SECURITY", "none").lower(),
            from_addr=os.getenv("SMTP_FROM", "churnradar@example.test"),
            from_name=os.getenv("SMTP_FROM_NAME", "ChurnRadar"),
            timeout_s=float(os.getenv("SMTP_TIMEOUT_S", "15")),
        )


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


@dataclass(slots=True)
class Mail:
    to: str
    subject: str
    text: str
    html: str | None = None
    reply_to: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)


def build_message(config: MailConfig, mail: Mail) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((config.from_name, config.from_addr))
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    msg["Message-ID"] = make_msgid(domain=config.from_addr.rpartition("@")[2] or None)
    if mail.reply_to:
        msg["Reply-To"] = mail.reply_to
    for name, value in mail.headers.items():
        msg[name] = value
    msg.set_content(mail.text)
    if mail.html:
        msg.add_alternative(mail.html, subtype="html")
    for item in mail.attachments:
        maintype, _, subtype = item.content_type.partition("/")
        msg.add_attachment(
            item.content, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=item.filename
        )
    return msg


def _send_sync(config: MailConfig, msg: EmailMessage) -> None:
    if config.security == "tls":
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(
            config.host, config.port, timeout=config.timeout_s, context=ssl.create_default_context()
        )
    else:
        smtp = smtplib.SMTP(config.host, config.port, timeout=config.timeout_s)
    with smtp:
        if config.security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if config.username:
            smtp.login(config.username, config.password)
        smtp.send_message(msg)


async def send_mail(config: MailConfig, mail: Mail) -> str:
    """Отправляет письмо и возвращает его Message-ID. Ошибки SMTP пробрасываются наружу."""
    if not config.enabled:
        raise MailDisabled("SMTP_HOST не задан")
    msg = build_message(config, mail)
    await asyncio.to_thread(_send_sync, config, msg)
    return str(msg["Message-ID"])
