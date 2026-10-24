"""«Что если»: предпросмотр PATCH настроек модели.

api проверяет изменение теми же правилами, что и PATCH, и собирает новую хранимую форму
настроек. Считает scoring (POST /internal/v1/model/preview): у него riskcore, доступ к
source.* и запас по CPU, а api остаётся тонким. В БД ничего не пишется.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import httpx
import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from svckit.auth import User
from svckit.errors import ApiError

from app import model_catalog, model_settings

log = structlog.get_logger()

SCORING_PREVIEW_PATH = "/internal/v1/model/preview"


async def proposed_storage(pool: asyncpg.Pool, patch: model_settings.ModelSettingsPatch) -> dict[str, Any]:
    """Хранимая форма настроек после патча; ошибки валидации — те же 422, что у PATCH."""
    current_api = await model_settings.get_settings(pool)
    current = model_catalog.storage_from_api(current_api)
    return model_settings.apply_patch(current, current_api["weights"], patch)


async def preview(
    request: Request, user: User, pool: asyncpg.Pool, patch: model_settings.ModelSettingsPatch
) -> JSONResponse:
    proposed = await proposed_storage(pool, patch)
    client: httpx.AsyncClient | None = getattr(request.app.state, "scoring_http", None)
    if client is None:
        raise ApiError(503, "scoring_unavailable", "Сервис оценки недоступен")
    # api уже проверил роль; scoring доверяет этим заголовкам так же, как сервисы — гейтвею
    headers = {"x-user-name": user.name, "x-user-roles": ",".join(sorted(user.roles))}
    if user.id:
        headers["x-user-id"] = user.id
    rid = getattr(request.state, "request_id", None)
    if rid:
        headers["x-request-id"] = rid
    try:
        resp = await client.post(SCORING_PREVIEW_PATH, json={"settings": proposed}, headers=headers)
    except httpx.TimeoutException as exc:
        raise ApiError(504, "preview_timeout", "Предпросмотр не уложился во время, попробуйте ещё раз") from exc
    except httpx.HTTPError as exc:
        log.warning("scoring_unreachable", error=str(exc))
        raise ApiError(503, "scoring_unavailable", "Сервис оценки недоступен") from exc
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ApiError(502, "bad_gateway", "Сервис оценки вернул некорректный ответ") from exc
    return JSONResponse(payload, status_code=resp.status_code)
