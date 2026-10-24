"""Swagger UI по единой спецификации contracts/openapi.yaml."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from app.config import settings
from app.errors import ApiError

router = APIRouter(tags=["ops"])

SWAGGER_VERSION = "5.17.14"

_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ChurnRadar API</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@{v}/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@{v}/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({{
      url: "/api/v1/openapi.yaml",
      dom_id: "#swagger-ui",
      deepLinking: true,
      withCredentials: true
    }});
  </script>
</body>
</html>
"""


@router.get("/api/v1/docs", include_in_schema=False)
async def swagger_ui() -> HTMLResponse:
    return HTMLResponse(_HTML.format(v=SWAGGER_VERSION))


@router.get("/api/v1/openapi.yaml", include_in_schema=False)
async def openapi_yaml() -> Response:
    path = Path(settings.openapi_path)
    if not path.is_file():
        raise ApiError(404, "not_found", "Спецификация API не найдена")
    return Response(path.read_bytes(), media_type="application/yaml; charset=utf-8")
