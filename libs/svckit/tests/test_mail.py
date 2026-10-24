import asyncio
from email import message_from_bytes
from email.policy import default

import pytest

from svckit.mail import Mail, MailConfig, MailDisabled, build_message, send_mail


class FakeSMTP:
    """Минимальный SMTP-сервер: принимает одно письмо и запоминает его."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.server: asyncio.base_events.Server | None = None
        self.port = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def say(line: str) -> None:
            writer.write(line.encode() + b"\r\n")

        say("220 fake")
        await writer.drain()
        data_mode = False
        chunks: list[bytes] = []
        while line := await reader.readline():
            if data_mode:
                if line in (b".\r\n", b".\n"):
                    self.received.append(b"".join(chunks))
                    data_mode = False
                    say("250 ok")
                else:
                    chunks.append(line[1:] if line.startswith(b"..") else line)
            else:
                cmd = line.strip().upper()
                if cmd.startswith((b"EHLO", b"HELO")):
                    say("250 fake")
                elif cmd == b"DATA":
                    data_mode = True
                    say("354 go")
                elif cmd == b"QUIT":
                    say("221 bye")
                    await writer.drain()
                    break
                else:
                    say("250 ok")
            await writer.drain()
        writer.close()

    async def __aenter__(self) -> "FakeSMTP":
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()


def test_build_message_has_both_parts_and_headers():
    cfg = MailConfig(host="x", from_addr="team@example.test", from_name="Команда")
    msg = build_message(cfg, Mail(to="a@example.test", subject="Тема", text="текст", html="<b>текст</b>",
                                  reply_to="m@example.test", headers={"X-Customer-Id": "5"}))
    assert msg["To"] == "a@example.test"
    assert "team@example.test" in msg["From"]
    assert msg["Reply-To"] == "m@example.test"
    assert msg["X-Customer-Id"] == "5"
    assert msg["Message-ID"].endswith("@example.test>")
    assert {p.get_content_type() for p in msg.iter_parts()} == {"text/plain", "text/html"}


async def test_send_without_host_is_disabled():
    with pytest.raises(MailDisabled):
        await send_mail(MailConfig(), Mail(to="a@example.test", subject="s", text="t"))


async def test_send_reaches_smtp_server():
    async with FakeSMTP() as smtp:
        cfg = MailConfig(host="127.0.0.1", port=smtp.port)
        msg_id = await send_mail(cfg, Mail(to="a@example.test", subject="Пополните баланс", text="Привет"))
    assert len(smtp.received) == 1
    got = message_from_bytes(smtp.received[0], policy=default)
    assert got["Subject"] == "Пополните баланс"
    assert got["Message-ID"] == msg_id
    assert got.get_content().strip() == "Привет"
