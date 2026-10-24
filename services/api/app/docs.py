"""Swagger UI по единой спецификации contracts/openapi.yaml.

Скрипт и стили Swagger UI отдаёт сам сервис: они приходят в образ вместе с pip-пакетом
fastapi-swagger (сборка swagger-ui-dist 5.x с поддержкой OpenAPI 3.1), в рантайме сеть не нужна.
"""

from __future__ import annotations

from functools import cache
from importlib import resources
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response
from svckit.errors import ApiError

from app.config import settings

router = APIRouter(tags=["ops"])

ASSETS = {
    "swagger-ui-bundle.js": "application/javascript; charset=utf-8",
    "swagger-ui.css": "text/css; charset=utf-8",
}

_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ChurnRadar API</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="/api/v1/docs/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="/api/v1/docs/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: "/api/v1/openapi.yaml",
      dom_id: "#swagger-ui",
      deepLinking: true,
      withCredentials: true,
      validatorUrl: null
    });
  </script>
</body>
</html>
"""


@router.get("/api/v1/docs", include_in_schema=False)
async def swagger_ui() -> HTMLResponse:
    return HTMLResponse(_HTML)


@cache
def _asset(name: str) -> bytes:
    return (resources.files("fastapi_swagger") / "resources" / name).read_bytes()


@router.get("/api/v1/docs/{name}", include_in_schema=False)
async def swagger_asset(name: str) -> Response:
    media_type = ASSETS.get(name)
    if media_type is None:
        raise ApiError(404, "not_found", "Не найдено")
    try:
        data = _asset(name)
    except (ModuleNotFoundError, FileNotFoundError) as exc:
        raise ApiError(404, "not_found", "Файлы Swagger UI не установлены") from exc
    return Response(data, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/v1/openapi.yaml", include_in_schema=False)
async def openapi_yaml() -> Response:
    path = Path(settings.openapi_path)
    if not path.is_file():
        raise ApiError(404, "not_found", "Спецификация API не найдена")
    return Response(path.read_bytes(), media_type="application/yaml; charset=utf-8")
