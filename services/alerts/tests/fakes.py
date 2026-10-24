"""Подставные внешние системы для интеграционных тестов: SMTP-сервер и Telegram Bot API."""

from __future__ import annotations

import asyncio
import email
import json
import threading
import time
from datetime import UTC, datetime
from email import policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


async def put_summary(
    conn: Any, *, loss: int, high: int = 1, share: float = 0.2, customers: int = 3, scored_at: datetime | None = None
) -> None:
    """Сводка scoring: из неё alerts берёт показатели базы."""
    data = {
        "expected_loss": loss,
        "by_level": {"high": high},
        "revenue_at_risk_share": share,
        "customers_total": customers,
    }
    await conn.execute(
        "INSERT INTO scoring.summary (id, data, model_version, scored_at) VALUES (1, $1::jsonb, 1, $2) "
        "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, scored_at = EXCLUDED.scored_at",
        json.dumps(data),
        scored_at or datetime.now(UTC),
    )


class FakeSmtp:
    """Минимальный SMTP-сервер в том же цикле событий: принимает письмо и складывает его в messages."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._session, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def say(line: str) -> None:
            writer.write((line + "\r\n").encode())

        say("220 fake smtp")
        rcpt: list[str] = []
        try:
            while line := (await reader.readline()).decode(errors="replace").strip():
                cmd = line.upper()
                if cmd.startswith("MAIL FROM"):
                    rcpt = []
                    say("250 ok")
                elif cmd.startswith("RCPT TO"):
                    rcpt.append(line.split(":", 1)[1].strip(" <>"))
                    say("250 ok")
                elif cmd == "DATA":
                    say("354 go")
                    await writer.drain()
                    body = b""
                    while (chunk := await reader.readline()) not in (b".\r\n", b""):
                        body += chunk
                    msg = email.message_from_bytes(body, policy=policy.default)
                    self.messages.append({"to": rcpt, "subject": str(msg["Subject"]), "message": msg})
                    say("250 queued")
                elif cmd == "QUIT":
                    say("221 bye")
                    await writer.drain()
                    break
                else:
                    say("250 ok")  # EHLO, HELO, NOOP, RSET
                await writer.drain()
        finally:
            writer.close()


class FakeTelegram:
    """Локальный Bot API: getMe, sendMessage (копит), getUpdates (отдаёт подложенные обновления)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.get_updates_calls = 0
        self._lock = threading.Lock()
        self._next_id = 1
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                method = self.path.rsplit("/", 1)[-1]
                result: Any = True
                if method == "getMe":
                    result = {"id": 1, "is_bot": True, "username": "churn_test_bot"}
                elif method == "sendMessage":
                    with outer._lock:
                        outer.sent.append(body)
                        result = {"message_id": len(outer.sent)}
                elif method == "getUpdates":
                    with outer._lock:
                        outer.get_updates_calls += 1
                        offset = int(body.get("offset") or 0)
                        result = [u for u in outer.updates if u["update_id"] >= offset]
                    if not result:
                        time.sleep(0.2)  # как long polling, только короче
                payload = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def say(self, chat_id: int, text: str, username: str = "anna_tg") -> None:
        with self._lock:
            self.updates.append(
                {
                    "update_id": self._next_id,
                    "message": {"chat": {"id": chat_id}, "from": {"username": username}, "text": text},
                }
            )
            self._next_id += 1

    def replies(self, chat_id: int) -> list[str]:
        with self._lock:
            return [m["text"] for m in self.sent if str(m["chat_id"]) == str(chat_id)]
