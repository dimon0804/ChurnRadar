"""Пробы: /healthz — процесс жив (liveness), /readyz — зависимости готовы (readiness)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ReadinessCheck = Callable[[Request], Awaitable[Mapping[str, bool]]]


def install_probes(app: FastAPI, readiness: ReadinessCheck) -> None:
    """readiness возвращает {"имя зависимости": готова}; /readyz отвечает 200, только если готовы все."""

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> JSONResponse:
        checks = dict(await readiness(request))
        ok = bool(checks) and all(checks.values())
        return JSONResponse(
            {"status": "ok" if ok else "fail", "checks": checks}, status_code=200 if ok else 503
        )
