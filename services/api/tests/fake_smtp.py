"""Минимальный SMTP-сервер для тестов: принимает письма и запоминает их (как в svckit/tests)."""

from __future__ import annotations

import asyncio
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default


class FakeSMTP:
    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.server: asyncio.base_events.Server | None = None
        self.port = 0

    @property
    def messages(self) -> list[EmailMessage]:
        return [message_from_bytes(raw, policy=default) for raw in self.received]  # type: ignore[misc]

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
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
                    chunks = []
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

    async def __aenter__(self) -> FakeSMTP:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()
