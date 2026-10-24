import hmac
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import metrics
from .config import Config
from .core import Core
from .model_settings import SettingsValidationError
from .schemas import ModelSettingsPatch, SimulatorCommand, SourceChangeBatch, SourceSnapshot
from .scoring import CustomerResult, ServiceResult
from .store import DataIntegrityError
from .timeutil import iso

LEVELS = ("high", "medium", "low", "unknown")
ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    422: "validation_error",
    503: "unavailable",
}

SWAGGER_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ChurnRadar API</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({ url: "/api/v1/openapi.yaml", dom_id: "#swagger-ui", deepLinking: true });
  </script>
</body>
</html>
"""


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        self.status, self.code, self.message, self.details = status, code, message, details


def error_body(code: str, message: str, details: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"error": err}


def current_user(request: Request) -> Optional[str]:
    return request.headers.get("x-user-name") or None


def require_admin(request: Request) -> Optional[str]:
    roles = {r.strip().lower() for r in request.headers.get("x-user-roles", "").split(",") if r.strip()}
    if "admin" not in roles:
        raise ApiError(403, "forbidden", "Недостаточно прав: действие доступно только администратору")
    return current_user(request)


# --- сериализация ---


def customer_summary(r: CustomerResult) -> Dict[str, Any]:
    a = r.account
    return {
        "id": a.id,
        "email": a.email,
        "balance": a.balance,
        "bonus_balance": a.bonus_balance,
        "bonus_state": a.bonus_state,
        "withdraw_balance": a.withdraw_balance,
        "total_replenished": a.total_replenished,
        "risk_score": r.score,
        "risk_level": r.level,
        "loyalty": r.loyalty,
        "reasons": r.reasons,
        "services_total": r.services_total,
        "services_at_risk": r.services_at_risk,
        "monthly_revenue": r.monthly_revenue,
        "expected_loss": r.expected_loss,
        "exposure": r.exposure,
    }


def service_risk(sr: ServiceResult) -> Dict[str, Any]:
    s = sr.service
    return {
        "id": s.id,
        "name": s.name,
        "status": s.status,
        "payment_term": s.payment_term,
        "price": s.price,
        "monthly_price": round(sr.monthly_price),
        "auto_prolong": s.auto_prolong,
        "created_at": iso(s.created_at),
        "expires_at": iso(s.expires_at),
        "days_to_expiry": round(sr.days_to_expiry, 2),
        "risk_score": sr.score,
        "risk_level": sr.level,
        "reasons": sr.reasons,
        "expected_loss": sr.expected_loss,
    }


SORT_KEYS = {
    "id": lambda r: r.account.id,
    "email": lambda r: r.account.email.lower(),
    "balance": lambda r: r.account.balance,
    "total_replenished": lambda r: r.account.total_replenished,
    "risk_score": lambda r: r.score,
    "expected_loss": lambda r: r.expected_loss,
}


def sort_results(items: List[CustomerResult], sort: str, order: str) -> List[CustomerResult]:
    key = SORT_KEYS[sort]
    present = [r for r in items if key(r) is not None]
    missing = [r for r in items if key(r) is None]
    # стабильная сортировка: сначала по id, потом по полю — равные значения идут по возрастанию id
    present.sort(key=lambda r: r.account.id)
    present.sort(key=key, reverse=(order == "desc"))
    missing.sort(key=lambda r: r.account.id)
    return present + missing


def create_app(config: Optional[Config] = None) -> FastAPI:
    config = config or Config()
    logging.basicConfig(
        level=config.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    core = Core(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await core.start()
        yield
        await core.stop()

    app = FastAPI(
        title="ChurnRadar mock-core",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.core = core

    # --- ошибки ---

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        code = ERROR_CODES.get(exc.status_code, "error")
        message = exc.detail if isinstance(exc.detail, str) else "Ошибка запроса"
        if exc.status_code == 404 and message == "Not Found":
            message = "Ресурс не найден"
        if exc.status_code == 405:
            message = "Метод не поддерживается"
        return JSONResponse(error_body(code, message), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        details = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
        ]
        return JSONResponse(
            error_body("validation_error", "Ошибка валидации запроса", jsonable_encoder(details)), status_code=422
        )

    def route_template(request: Request) -> str:
        endpoint = request.scope.get("endpoint")
        for route in app.routes:
            if getattr(route, "endpoint", None) is endpoint and endpoint is not None:
                return route.path
        return "unmatched"

    @app.middleware("http")
    async def http_metrics(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            path = route_template(request)
            metrics.http_requests.labels(config.service_name, request.method, path, str(status)).inc()
            metrics.http_duration.labels(config.service_name, path).observe(time.perf_counter() - started)

    # --- служебные ---

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        body = {"status": "ready" if core.ready else "starting", "nats": core.bus.connected}
        return JSONResponse(body, status_code=200 if core.ready else 503)

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(request: Request):
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), config.metrics_token):
            return JSONResponse(
                error_body("unauthorized", "Нужен токен сборщика метрик"),
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return Response(metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/api/v1/docs", include_in_schema=False)
    async def docs():
        return HTMLResponse(SWAGGER_HTML)

    @app.get("/api/v1/openapi.yaml", include_in_schema=False)
    async def openapi_yaml():
        try:
            content = config.contract_path.read_bytes()
        except OSError:
            raise ApiError(404, "not_found", "Файл спецификации не найден")
        return Response(content, media_type="application/yaml")

    # --- клиенты ---

    @app.get("/api/v1/customers")
    async def list_customers(
        sort: Literal["id", "email", "balance", "total_replenished", "risk_score", "expected_loss"] = "risk_score",
        order: Literal["asc", "desc"] = "desc",
        level: Optional[str] = None,
        search: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(25, ge=1, le=200),
    ):
        items = list(core.results.values())
        if level:
            wanted = {v.strip().lower() for v in level.split(",") if v.strip()}
            bad = sorted(wanted - set(LEVELS))
            if bad:
                raise ApiError(
                    422,
                    "validation_error",
                    "Неизвестный уровень риска: " + ", ".join(bad),
                    [{"loc": ["query", "level"], "msg": f"допустимо: {', '.join(LEVELS)}"}],
                )
            items = [r for r in items if r.level in wanted]
        if search and search.strip():
            needle = search.strip().lower()
            exact_id = int(needle) if needle.isdigit() else None
            items = [r for r in items if needle in r.account.email.lower() or r.account.id == exact_id]
        items = sort_results(items, sort, order)
        start = (page - 1) * page_size
        return {
            "items": [customer_summary(r) for r in items[start : start + page_size]],
            "total": len(items),
            "page": page,
            "page_size": page_size,
            "scored_at": iso(core.scored_at),
            "model_version": core.settings.version,
        }

    @app.get("/api/v1/customers/{id}")
    async def get_customer(id: int):  # noqa: A002 - имя параметра из контракта
        customer_id = id
        r = core.results.get(customer_id)
        if r is None:
            raise ApiError(404, "not_found", f"Клиент {customer_id} не найден")
        body = customer_summary(r)
        body.update(
            {
                "services": [service_risk(sr) for sr in r.services],
                "features": r.features,
                "transactions": [
                    {
                        "id": t.id,
                        "type": t.type,
                        "status": t.status,
                        "amount": t.amount,
                        "created_at": iso(t.created_at),
                        "performed_at": iso(t.performed_at),
                        "service_id": t.service_id,
                    }
                    for t in core.store.account_transactions(customer_id)[:50]
                ],
                "money": {
                    "monthly_revenue": r.monthly_revenue,
                    "exposure": r.exposure,
                    "expected_loss": r.expected_loss,
                    "balance_coverage_days": r.balance_coverage_days,
                    "explanation": r.money_explanation,
                },
                "recommendations": r.recommendations,
                "scored_at": iso(core.scored_at),
                "model_version": core.settings.version,
            }
        )
        return body

    @app.get("/api/v1/analytics/summary")
    async def analytics_summary():
        body = dict(core.full_summary())
        body["scored_at"] = iso(core.scored_at)
        body["model_version"] = core.settings.version
        return body

    # --- модель ---

    @app.get("/api/v1/model/settings")
    async def get_settings():
        return core.settings.to_api()

    @app.patch("/api/v1/model/settings")
    async def patch_settings(patch: ModelSettingsPatch, user: Optional[str] = Depends(require_admin)):
        try:
            return await core.patch_settings(patch, user)
        except SettingsValidationError as exc:
            raise ApiError(422, "validation_error", str(exc), exc.details)

    @app.post("/api/v1/model/settings/reset")
    async def reset_settings(user: Optional[str] = Depends(require_admin)):
        return await core.reset_settings(user)

    @app.get("/api/v1/model/settings/history")
    async def settings_history(limit: int = Query(50, ge=1, le=200)):
        return {"items": core.settings.history_items(limit)}

    # --- источник ---

    @app.put("/api/v1/source/snapshot", status_code=202)
    async def put_snapshot(snapshot: SourceSnapshot, user: Optional[str] = Depends(require_admin)):
        try:
            return await core.replace_snapshot(snapshot)
        except DataIntegrityError as exc:
            raise ApiError(422, "validation_error", str(exc), exc.details)

    @app.post("/api/v1/source/events", status_code=202)
    async def post_events(batch: SourceChangeBatch, user: Optional[str] = Depends(require_admin)):
        try:
            return {"accepted": await core.apply_changes(batch)}
        except DataIntegrityError as exc:
            raise ApiError(422, "validation_error", str(exc), exc.details)

    # --- симулятор ---

    @app.get("/api/v1/simulator")
    async def get_simulator():
        return core.simulator_state()

    @app.put("/api/v1/simulator")
    async def put_simulator(command: SimulatorCommand, user: Optional[str] = Depends(require_admin)):
        if command.generate is not None:
            await core.generate(command.generate.accounts, command.generate.seed)
        core.set_simulator(running=command.running, interval_ms=command.interval_ms)
        return core.simulator_state()

    return app


app = create_app()
