"""Уведомление → письмо (текст + HTML) и сообщение Telegram."""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlsplit

from .notify import Notice

SETTINGS_PATH = "/notifications"


def public_link_ok(url: str) -> bool:
    """Telegram не принимает кнопки со ссылкой на localhost и внутренние адреса."""
    host = (urlsplit(url).hostname or "").lower()
    return bool(host) and host not in {"localhost", "127.0.0.1", "::1"} and "." in host


def email_payload(notice: Notice, public_url: str) -> dict[str, str]:
    link = public_url + notice.link
    settings = public_url + SETTINGS_PATH
    text_lines = [notice.title, "", notice.text]
    if notice.lines:
        text_lines += ["", *notice.lines]
    text_lines += ["", f"Открыть в панели: {link}", "", f"Настроить уведомления: {settings}"]
    lines_html = ""
    if notice.lines:
        items = "".join(f"<li style='margin:2px 0'>{escape(line.strip())}</li>" for line in notice.lines)
        lines_html = f"<ul style='padding-left:18px;margin:12px 0;color:#333'>{items}</ul>"
    html = f"""<!doctype html>
<html lang="ru"><body style="margin:0;background:#f4f4f2;font-family:Arial,Helvetica,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px">
<table role="presentation" width="560" cellpadding="0" cellspacing="0"
 style="background:#fff;border-radius:12px;padding:24px">
<tr><td>
<p style="margin:0 0 4px;font-size:12px;color:#777">ChurnRadar</p>
<h1 style="margin:0 0 12px;font-size:20px;color:#111">{escape(notice.title)}</h1>
<p style="margin:0;font-size:14px;line-height:20px;color:#333">{escape(notice.text)}</p>
{lines_html}
<p style="margin:20px 0"><a href="{escape(link)}" style="background:#111;color:#fff;text-decoration:none;
padding:10px 16px;border-radius:8px;font-size:14px;display:inline-block">Открыть в панели</a></p>
<p style="margin:16px 0 0;font-size:12px;color:#888">Это письмо пришло, потому что вы включили
уведомления в ChurnRadar. <a href="{escape(settings)}" style="color:#888">Настроить уведомления</a></p>
</td></tr></table></td></tr></table></body></html>"""
    return {"subject": f"ChurnRadar: {notice.title}", "text": "\n".join(text_lines), "html": html}


def telegram_payload(notice: Notice, public_url: str) -> dict[str, Any]:
    link = public_url + notice.link
    parts = [f"<b>{escape(notice.title)}</b>", escape(notice.text)]
    if notice.lines:
        parts.append("\n".join(escape(line) for line in notice.lines))
    payload: dict[str, Any] = {"text": "\n\n".join(parts), "parse_mode": "HTML"}
    if public_link_ok(link):
        payload["button"] = {"text": "Открыть в панели", "url": link}
    else:
        payload["text"] += f"\n\n{escape(link)}"
    return payload
