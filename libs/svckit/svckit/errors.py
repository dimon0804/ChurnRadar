"""Единый формат ошибок: {"error": {"code", "message", "details", "request_id"}}."""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger()

REQUEST_ID_HEADER = "X-Request-Id"

STATUS_CODES: dict[int, tuple[str, str]] = {
    400: ("bad_request", "Некорректный запрос"),
    401: ("unauthorized", "Требуется вход в систему"),
    403: ("forbidden", "Недостаточно прав"),
    404: ("not_found", "Не найдено"),
    405: ("method_not_allowed", "Метод не поддерживается"),
    409: ("conflict", "Конфликт данных"),
    413: ("payload_too_large", "Тело запроса слишком большое"),
    415: ("unsupported_media_type", "Неподдерживаемый формат тела запроса"),
    422: ("validation_error", "Ошибка валидации"),
    429: ("too_many_requests", "Слишком много запросов"),
    500: ("internal_error", "Внутренняя ошибка сервиса"),
    502: ("bad_gateway", "Смежный сервис ответил ошибкой"),
    503: ("service_unavailable", "Сервис временно недоступен"),
}


class ApiError(StarletteHTTPException):
    """Ошибка с кодом и сообщением для клиента.

    Наследник HTTPException: FastAPI пропускает такие исключения из чтения тела запроса
    как есть, а не превращает в 400 (важно для лимитов и распаковки тела).
    """

    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(status_code=status, detail=message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def __str__(self) -> str:
        return self.message


def request_id_of(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid or request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex


def error_response(
    request: Request, status: int, code: str, message: str, details: Any = None
) -> JSONResponse:
    rid = request_id_of(request)
    body: dict[str, Any] = {"error": {"code": code, "message": message, "request_id": rid}}
    if details is not None:
        body["error"]["details"] = jsonable_encoder(details)
    return JSONResponse(status_code=status, content=body, headers={REQUEST_ID_HEADER: rid})


async def send_error(scope: Scope, send: Send, status: int, code: str, message: str) -> None:
    """Ответ-ошибка прямо из ASGI-middleware, до маршрутизации.

    Заголовок X-Request-Id добавит RequestIdMiddleware.
    """
    rid = scope.get("state", {}).get("request_id")
    body = json.dumps(
        {"error": {"code": code, "message": message, "request_id": rid}}, ensure_ascii=False
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def validation_details(errors: Any) -> list[dict[str, Any]]:
    out = []
    for err in errors:
        loc = [str(p) for p in err.get("loc", ())]
        out.append(
            {
                "field": ".".join(loc[1:])
                if loc and loc[0] in {"body", "query", "path", "header"}
                else ".".join(loc),
                "location": loc[0] if loc else None,
                "message": err.get("msg"),
                "type": err.get("type"),
            }
        )
    return out


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            request, 422, "validation_error", "Ошибка валидации запроса", validation_details(exc.errors())
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = STATUS_CODES.get(exc.status_code, ("error", "Ошибка запроса"))
        response = error_response(request, exc.status_code, code, message)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        code, message = STATUS_CODES[500]
        return error_response(request, 500, code, message)


class RequestIdMiddleware:
    """Проставляет request_id в request.state, в контекст логов и в заголовок ответа."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                rid = value.decode("latin-1")[:128]
                break
        rid = rid or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid
        structlog.contextvars.bind_contextvars(request_id=rid)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(k.lower() == b"x-request-id" for k, _ in headers):
                    headers.append((b"x-request-id", rid.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
