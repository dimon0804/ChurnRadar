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
    return model_settings.apply_patch(current, current_api["weights"], patch, current_api.get("groups", []))


def _headers(request: Request, user: User) -> dict[str, str]:
    # api уже проверил роль; scoring доверяет этим заголовкам так же, как сервисы — гейтвею
    headers = {"x-user-name": user.name, "x-user-roles": ",".join(sorted(user.roles))}
    if user.id:
        headers["x-user-id"] = user.id
    rid = getattr(request.state, "request_id", None)
    if rid:
        headers["x-request-id"] = rid
    return headers


async def compare(
    request: Request, user: User, pool: asyncpg.Pool, version_from: int, version_to: int
) -> dict[str, Any]:
    """Сравнение двух версий: различия из журнала и эффект на текущих данных.

    Эффект считает тот же предпросмотр scoring: «было» по настройкам from, «станет» по to.
    Если scoring недоступен или занят, различия всё равно отдаём, а impact = null.
    """
    result = await model_settings.compare_versions(pool, version_from, version_to)
    old, new = result.pop("old_storage"), result.pop("new_storage")
    for key in ("from", "to"):
        at = result[key]["changed_at"]
        result[key]["changed_at"] = None if at is None else at.isoformat().replace("+00:00", "Z")
    client: httpx.AsyncClient | None = getattr(request.app.state, "scoring_http", None)
    impact = None
    if client is not None and result["changes"]:
        try:
            resp = await client.post(
                SCORING_PREVIEW_PATH, json={"settings": new, "baseline": old}, headers=_headers(request, user)
            )
            if resp.status_code == 200:
                impact = resp.json()
            else:
                log.warning("compare_preview_failed", status=resp.status_code)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("compare_preview_failed", error=str(exc))
    result["impact"] = impact
    return result


async def preview(
    request: Request, user: User, pool: asyncpg.Pool, patch: model_settings.ModelSettingsPatch
) -> JSONResponse:
    proposed = await proposed_storage(pool, patch)
    client: httpx.AsyncClient | None = getattr(request.app.state, "scoring_http", None)
    if client is None:
        raise ApiError(503, "scoring_unavailable", "Сервис оценки недоступен")
    try:
        resp = await client.post(SCORING_PREVIEW_PATH, json={"settings": proposed}, headers=_headers(request, user))
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
