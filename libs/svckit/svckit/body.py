"""Тело запроса: лимиты размера, потоковая распаковка Content-Encoding: gzip, разбор JSON.

max_bytes ограничивает тело после распаковки (несжатое — как пришло по сети),
max_compressed_bytes — сжатое тело на входе. Распаковка идёт кусками по мере чтения,
и выход каждого куска обрезается остатком лимита: gzip-бомба упирается в 413, не раздувая память.
"""

from __future__ import annotations

import email.message
import zlib

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import TypeAdapter, ValidationError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import ApiError, send_error

_MB = 1024 * 1024
_GZIP_WBITS = 16 + zlib.MAX_WBITS


def _mb(n: int) -> str:
    return f"{n / _MB:g}"


class _Inflater:
    """Распаковка gzip по кускам с потолком на суммарный выход; несколько gzip-членов подряд допустимы."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.produced = 0
        self._d = zlib.decompressobj(_GZIP_WBITS)
        self._started = False

    def feed(self, data: bytes) -> bytes:
        parts: list[bytes] = []
        try:
            while data:
                if self._d.eof:
                    self._d = zlib.decompressobj(_GZIP_WBITS)
                self._started = True
                piece = self._d.decompress(data, self.limit - self.produced + 1)
                self._take(piece, parts)
                data = self._d.unused_data if self._d.eof else b""
        except zlib.error as exc:
            raise ApiError(400, "bad_request", "Не удалось распаковать тело запроса (gzip)") from exc
        return b"".join(parts)

    def finish(self) -> bytes:
        parts: list[bytes] = []
        try:
            self._take(self._d.flush(), parts)
        except zlib.error as exc:
            raise ApiError(400, "bad_request", "Не удалось распаковать тело запроса (gzip)") from exc
        if not self._started or not self._d.eof:
            raise ApiError(400, "bad_request", "Тело запроса в gzip оборвано или пусто")
        return b"".join(parts)

    def _take(self, piece: bytes, parts: list[bytes]) -> None:
        self.produced += len(piece)
        if self.produced > self.limit:
            raise ApiError(
                413, "payload_too_large", f"Тело запроса после распаковки больше {_mb(self.limit)} МБ"
            )
        if piece:
            parts.append(piece)


class RequestBodyMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int, max_compressed_bytes: int | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.max_compressed_bytes = max_compressed_bytes if max_compressed_bytes is not None else max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        encoding = b""
        declared: int | None = None
        for name, value in scope.get("headers", []):
            if name == b"content-encoding":
                encoding = value.strip().lower()
            elif name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None

        if encoding in (b"", b"identity"):
            gzipped = False
        elif encoding in (b"gzip", b"x-gzip"):
            gzipped = True
        else:
            await send_error(
                scope, send, 415, "unsupported_media_type", "Из Content-Encoding поддерживается только gzip"
            )
            return

        wire_limit = self.max_compressed_bytes if gzipped else self.max_bytes
        what = "Сжатое тело запроса" if gzipped else "Тело запроса"
        if declared is not None and declared > wire_limit:
            await send_error(scope, send, 413, "payload_too_large", f"{what} больше {_mb(wire_limit)} МБ")
            return

        received = 0
        inflater = _Inflater(self.max_bytes) if gzipped else None
        finished = False

        async def limited_receive() -> Message:
            nonlocal received, finished
            message = await receive()
            if message["type"] != "http.request" or finished:
                return message
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > wire_limit:
                raise ApiError(413, "payload_too_large", f"{what} больше {_mb(wire_limit)} МБ")
            if inflater is None:
                return message
            more = message.get("more_body", False)
            body = inflater.feed(chunk)
            if not more:
                body += inflater.finish()
                finished = True
            return {"type": "http.request", "body": body, "more_body": more}

        if gzipped:
            # дальше приложение видит обычное несжатое тело
            scope = dict(scope)
            scope["headers"] = [
                (k, v)
                for k, v in scope.get("headers", [])
                if k not in (b"content-encoding", b"content-length")
            ]
        await self.app(scope, limited_receive, send)


async def read_body(request: Request) -> bytearray:
    """Тело целиком в один bytearray (без промежуточного списка кусков, как в request.body())."""
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
    return buf


def is_json_content_type(value: str | None) -> bool:
    """Как у FastAPI: без Content-Type тело считаем JSON; иначе application/json или application/*+json."""
    if not value:
        return True
    message = email.message.Message()
    message["content-type"] = value
    if message.get_content_maintype() != "application":
        return False
    subtype = message.get_content_subtype()
    return subtype == "json" or subtype.endswith("+json")


def parse_json_body[T](request: Request, raw: bytes | bytearray, adapter: TypeAdapter[T]) -> T:
    """JSON → модель за один проход pydantic-core, без промежуточных dict/list в Python.

    Ошибки — как у обычного тела FastAPI (RequestValidationError с loc от "body").
    """
    if not is_json_content_type(request.headers.get("content-type")):
        raise ApiError(
            422,
            "validation_error",
            "Ошибка валидации запроса",
            [{"field": "", "location": "body", "message": "Ожидается JSON", "type": "content_type"}],
        )
    try:
        return adapter.validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False, include_context=False)
        raise RequestValidationError([{**e, "loc": ("body", *e["loc"])} for e in errors]) from None
