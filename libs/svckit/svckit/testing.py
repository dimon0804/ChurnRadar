"""Помощники для интеграционных тестов сервисов. В рабочем коде не используются."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from urllib.parse import urlsplit


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class TcpGate:
    """TCP-прокси, который можно закрыть и открыть: так NATS для сервиса пропадает и возвращается.

    Сервис подключается к gate.url; пока шлюз закрыт, порт никто не слушает (connection refused),
    при закрытии рвутся и уже открытые соединения.
    """

    def __init__(self, target_url: str) -> None:
        parts = urlsplit(target_url)
        self.scheme = parts.scheme or "nats"
        self.host = parts.hostname or "127.0.0.1"
        self.target_port = parts.port or 4222
        self.port = free_port()
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def url(self) -> str:
        return f"{self.scheme}://127.0.0.1:{self.port}"

    @property
    def is_open(self) -> bool:
        return self._server is not None

    async def open(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()
        if server is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), timeout=2)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(self.host, self.target_port)
        except OSError:
            writer.close()
            return
        self._writers.update((writer, up_writer))

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            with contextlib.suppress(Exception):
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            dst.close()

        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))
        self._writers.discard(writer)
        self._writers.discard(up_writer)
