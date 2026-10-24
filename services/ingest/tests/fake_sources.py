"""Фейковые HTTP-источники для тестов коннекторов: биллинг по contracts/billing-source.openapi.yaml
и Logs API Метрики. Подключаются к планировщику через httpx.ASGITransport, без сети."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse


@dataclass
class BillingState:
    token: str = "billing-token"
    accounts: list[dict[str, Any]] = field(default_factory=list)
    services: list[dict[str, Any]] = field(default_factory=list)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    fail_next: int = 0          # сколько следующих запросов ответить 503
    fail_status: int = 503
    requests: list[dict[str, Any]] = field(default_factory=list)


def billing_app(state: BillingState) -> FastAPI:
    app = FastAPI()

    @app.get("/v1/{entity}")
    async def page(entity: str, request: Request) -> Any:
        params = dict(request.query_params)
        state.requests.append({"entity": entity, **params})
        if request.headers.get("authorization") != f"Bearer {state.token}":
            return JSONResponse({"error": {"code": "unauthorized", "message": "нет токена"}}, status_code=401)
        if state.fail_next > 0:
            state.fail_next -= 1
            return JSONResponse({"error": {"code": "busy", "message": "занято"}}, status_code=state.fail_status)
        rows = sorted(getattr(state, entity), key=lambda r: (r["updatedAt"], r["id"]))
        since = params.get("updated_since")
        if since:
            rows = [r for r in rows if r["updatedAt"] >= since]
        start = int(params.get("cursor") or 0)
        limit = int(params.get("limit") or 500)
        chunk = rows[start : start + limit]
        more = start + limit < len(rows)
        return {"items": chunk, "next_cursor": str(start + limit) if more else None}

    return app


@dataclass
class MetricaState:
    token: str = "metrica-token"
    counter_id: int = 100500
    tsv_parts: list[str] = field(default_factory=list)
    polls_before_ready: int = 1
    final_status: str = "processed"
    created: list[dict[str, str]] = field(default_factory=list)
    cleaned: list[int] = field(default_factory=list)
    polls: int = 0


def metrica_app(state: MetricaState) -> FastAPI:
    app = FastAPI()
    base = "/management/v1/counter/{counter_id}"

    def denied(request: Request, counter_id: int) -> JSONResponse | None:
        if request.headers.get("authorization") != f"OAuth {state.token}":
            return JSONResponse({"errors": [{"error_type": "invalid_token", "message": "Неверный токен"}],
                                 "code": 403, "message": "Неверный токен"}, status_code=403)
        if counter_id != state.counter_id:
            return JSONResponse({"errors": [{"error_type": "access_denied", "message": "Нет доступа"}],
                                 "code": 403, "message": "Нет доступа"}, status_code=403)
        return None

    def info(request_id: int, status: str) -> dict[str, Any]:
        return {"log_request": {
            "request_id": request_id, "counter_id": state.counter_id, "source": "visits", "status": status,
            "size": sum(len(p) for p in state.tsv_parts),
            "parts": [{"part_number": n, "size": len(p)} for n, p in enumerate(state.tsv_parts)],
        }}

    @app.get(base + "/logrequests/evaluate")
    async def evaluate(counter_id: int, request: Request) -> Any:
        return denied(request, counter_id) or {"log_request_evaluation": {"possible": True,
                                                                          "max_possible_day_quantity": 366}}

    @app.post(base + "/logrequests")
    async def create(counter_id: int, request: Request) -> Any:
        if (resp := denied(request, counter_id)) is not None:
            return resp
        state.created.append(dict(request.query_params))
        return info(len(state.created), "created")

    @app.get(base + "/logrequest/{request_id}")
    async def status(counter_id: int, request_id: int, request: Request) -> Any:
        if (resp := denied(request, counter_id)) is not None:
            return resp
        state.polls += 1
        ready = state.polls > state.polls_before_ready
        return info(request_id, state.final_status if ready else "created")

    @app.get(base + "/logrequest/{request_id}/part/{part}/download")
    async def download(counter_id: int, request_id: int, part: int, request: Request) -> Any:
        if (resp := denied(request, counter_id)) is not None:
            return resp
        return PlainTextResponse(state.tsv_parts[part])

    @app.post(base + "/logrequest/{request_id}/clean")
    async def clean(counter_id: int, request_id: int, request: Request) -> Any:
        if (resp := denied(request, counter_id)) is not None:
            return resp
        state.cleaned.append(request_id)
        return info(request_id, "cleaned_by_user")

    return app


class Router:
    """Один ASGI на несколько фейковых хостов: billing.test и metrica.test."""

    def __init__(self, apps: dict[str, FastAPI]) -> None:
        self.apps = apps

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        host = dict(scope.get("headers") or []).get(b"host", b"").decode().split(":")[0]
        await self.apps[host](scope, receive, send)


METRICA_FIELDS = (
    "ym:s:visitID", "ym:s:dateTime", "ym:s:clientID", "ym:s:startURL", "ym:s:endURL", "ym:s:goalsID",
    "ym:s:parsedParamsKey1", "ym:s:parsedParamsKey2",
)


def visit_row(visit_id: int, dt: str, *, account: int | None = None, client: int = 1, end: str = "/services",
              goals: str = "[]") -> str:
    keys = "['account_id']" if account else "[]"
    values = f"['{account}']" if account else "[]"
    return "\t".join([str(visit_id), dt, str(client), "https://panel.test/services", f"https://panel.test{end}",
                      goals, keys, values])


def tsv(rows: list[str]) -> str:
    return "\n".join(["\t".join(METRICA_FIELDS), *rows]) + "\n"
