import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import parse_qs, quote, urlsplit

import asyncpg
import httpx
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, get_settings
from .observability import LOGINS, VERIFY, RequestContextMiddleware, request_id_var, setup_logging
from .oidc import OidcClient, OidcError, pkce_pair
from .roles import map_roles
from .saml import NAME_ATTRS, USERNAME_ATTRS, SamlClient, SamlError, new_request_id
from .sessions import (
    SAML_REQUEST_PREFIX,
    LoginRateLimiter,
    OidcStateStore,
    ReplayGuard,
    Session,
    SessionStore,
    now,
)
from .users import PostgresUserRepo, UserRepo

log = logging.getLogger("auth")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ADMIN_ONLY_PREFIXES = ("/api/v1/model", "/api/v1/source", "/api/v1/simulator")

hasher = PasswordHasher()
# verified against when the username is unknown, so response time does not reveal valid logins
DUMMY_HASH = hasher.hash(secrets.token_hex(16))


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status, self.code, self.message, self.headers = status, code, message, headers


def error_response(status: int, code: str, message: str, headers: dict | None = None, details=None):
    body = {"error": {"code": code, "message": message, "request_id": request_id_var.get()}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status, headers=headers)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class State:
    settings: Settings
    repo: UserRepo
    redis: Redis
    http: httpx.AsyncClient
    sessions: SessionStore
    limiter: LoginRateLimiter
    oidc_states: OidcStateStore
    oidc: OidcClient
    saml_requests: OidcStateStore
    saml_replay: ReplayGuard
    saml: SamlClient


async def seed_users(state: State) -> None:
    s = state.settings
    seeds = [(s.admin_username, s.admin_password, "Администратор", ["admin"])]
    if s.analyst_username:
        seeds.append((s.analyst_username, s.analyst_password, "Аналитик", ["analyst"]))
    for username, password, display, roles in seeds:
        if (
            username
            and password
            and await state.repo.ensure_password_user(username, hasher.hash(password), display, roles)
        ):
            log.info("seeded user", extra={"fields": {"username": username, "roles": roles}})


async def connect_pool(dsn: str) -> asyncpg.Pool:
    for attempt in range(30):
        try:
            return await asyncpg.create_pool(dsn, min_size=1, max_size=10)
        except (OSError, asyncpg.PostgresError) as exc:
            log.warning("postgres not ready", extra={"fields": {"attempt": attempt, "error": str(exc)}})
            await asyncio.sleep(2)
    raise RuntimeError("postgres is unreachable")


def create_app(
    settings: Settings | None = None,
    repo: UserRepo | None = None,
    redis: Redis | None = None,
    http: httpx.AsyncClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    state = State()
    state.settings = settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = None
        state.redis = redis or Redis.from_url(settings.redis_url, decode_responses=True)
        if repo is None:
            pool = await connect_pool(settings.asyncpg_dsn)
            state.repo = PostgresUserRepo(pool)
        else:
            state.repo = repo
        state.http = http or httpx.AsyncClient()
        state.sessions = SessionStore(state.redis, settings.session_ttl_seconds)
        state.limiter = LoginRateLimiter(state.redis, settings.login_max_attempts, settings.login_window_seconds)
        state.oidc_states = OidcStateStore(state.redis)
        state.oidc = OidcClient(settings, state.http)
        state.saml_requests = OidcStateStore(state.redis, prefix=SAML_REQUEST_PREFIX)
        state.saml_replay = ReplayGuard(state.redis)
        state.saml = SamlClient(settings, state.http)
        await seed_users(state)
        yield
        if http is None:
            await state.http.aclose()
        if redis is None:
            await state.redis.aclose()
        if pool is not None:
            await pool.close()

    app = FastAPI(title="ChurnRadar auth", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestContextMiddleware)
    app.state.ctx = state

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return error_response(422, "validation_error", "Проверьте логин и пароль", details=details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "error")
        return error_response(exc.status_code, code, str(exc.detail))

    def ctx() -> State:
        return state

    Ctx = Annotated[State, Depends(ctx)]

    def client_ip(request: Request) -> str:
        return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")

    def extract_token(request: Request) -> tuple[str | None, str]:
        auth = request.headers.get("authorization", "")
        if auth[:7].lower() == "bearer ":
            return auth[7:].strip(), "bearer"
        return request.cookies.get(settings.cookie_name), "cookie"

    def same_origin(request: Request) -> bool:
        source = request.headers.get("origin") or request.headers.get("referer")
        if not source:
            return False
        parts = urlsplit(source)
        origin = f"{parts.scheme}://{parts.netloc}".lower()
        allowed = {settings.public_url.rstrip("/").lower()}
        fwd_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if fwd_host:
            proto = request.headers.get("x-forwarded-proto", "http")
            allowed.add(f"{proto}://{fwd_host}".lower())
        return origin in allowed

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            settings.cookie_name,
            token,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/",
        )

    async def current_session(request: Request, st: Ctx) -> Session:
        token, _ = extract_token(request)
        session = await st.sessions.get(token) if token else None
        if session is None:
            raise ApiError(401, "unauthorized", "Нужно войти в систему")
        return session

    # ---- public auth API -------------------------------------------------------------

    @app.post("/api/v1/auth/login")
    async def login(body: LoginRequest, request: Request, st: Ctx):
        if request.headers.get("origin") and not same_origin(request):
            raise ApiError(403, "csrf_rejected", "Запрос пришёл с чужого сайта")
        ip = client_ip(request)
        retry = await st.limiter.retry_after(ip, body.username)
        if retry:
            LOGINS.labels("password", "rate_limited").inc()
            raise ApiError(
                429,
                "too_many_attempts",
                f"Слишком много попыток входа. Попробуйте через {retry} с",
                {"Retry-After": str(retry)},
            )

        user = await st.repo.get_password_user(body.username)
        ok = False
        try:
            ok = hasher.verify(user.password_hash if user and user.password_hash else DUMMY_HASH, body.password)
        except (VerificationError, InvalidHashError):
            ok = False
        ok = ok and user is not None and user.is_active

        await st.repo.record_login(body.username, "password", ok, ip)
        if not ok:
            await st.limiter.register_failure(ip, body.username)
            LOGINS.labels("password", "failure").inc()
            raise ApiError(401, "invalid_credentials", "Неверный логин или пароль")

        await st.limiter.reset(ip, body.username)
        if user.password_hash and hasher.check_needs_rehash(user.password_hash):
            log.info("password hash parameters outdated", extra={"fields": {"username": user.username}})
        session = Session(user.id, user.username, user.display_name, user.roles, "password", now())
        token = await st.sessions.create(session)
        LOGINS.labels("password", "success").inc()
        response = JSONResponse(session.public())
        set_session_cookie(response, token)
        return response

    @app.get("/api/v1/auth/me")
    async def me(session: Annotated[Session, Depends(current_session)]):
        return session.public()

    @app.post("/api/v1/auth/logout", status_code=204)
    async def logout(request: Request, st: Ctx, session: Annotated[Session, Depends(current_session)]):
        token, kind = extract_token(request)
        if kind == "cookie" and not same_origin(request):
            raise ApiError(403, "csrf_rejected", "Запрос пришёл с чужого сайта")
        await st.sessions.delete(token)
        response = Response(status_code=204)
        response.delete_cookie(settings.cookie_name, path="/")
        return response

    @app.get("/api/v1/auth/providers")
    async def providers(st: Ctx):
        oidc_up, saml_up = await asyncio.gather(st.oidc.available(), st.saml.available())
        oidc: dict = {"enabled": oidc_up}
        if oidc_up:
            oidc.update(display_name=settings.oidc_display_name, login_url="/api/v1/auth/oidc/login")
        saml: dict = {"enabled": saml_up}
        if saml_up:
            saml.update(display_name=settings.saml_display_name, login_url="/api/v1/auth/saml/login")
        return {"password": True, "oidc": oidc, "saml": saml}

    async def start_sso_session(
        st: State,
        method: str,
        subject: str,
        username: str,
        display: str,
        roles: list[str],
        ip: str,
        next_url: str,
        status_code: int = 302,
    ) -> Response:
        """Same session as after a password login, then back to the panel."""
        if not roles:
            log.warning("sso user without panel role", extra={"fields": {"method": method, "username": username}})
            LOGINS.labels(method, "forbidden").inc()
            await st.repo.record_login(username, method, False, ip)
            return RedirectResponse("/login?error=forbidden", status_code=status_code)
        user = await st.repo.upsert_sso_user(method, subject, username, display, roles)
        await st.repo.record_login(user.username, method, True, ip)
        session = Session(user.id, user.username, user.display_name, user.roles, method, now())
        token = await st.sessions.create(session)
        LOGINS.labels(method, "success").inc()
        response = RedirectResponse(next_url, status_code=status_code)
        set_session_cookie(response, token)
        return response

    def safe_next(value: str | None) -> str:
        if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
            return "/"
        return value

    @app.get("/api/v1/auth/oidc/login")
    async def oidc_login(st: Ctx, next: str = "/"):
        if not await st.oidc.available():
            raise ApiError(404, "oidc_disabled", "Вход через SSO сейчас недоступен")
        state_token = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        try:
            url = await st.oidc.authorize_url(state_token, nonce, challenge)
        except OidcError as exc:
            raise ApiError(404, "oidc_disabled", "Вход через SSO сейчас недоступен") from exc
        await st.oidc_states.put(state_token, {"verifier": verifier, "nonce": nonce, "next": safe_next(next)})
        return RedirectResponse(url, status_code=302)

    @app.get("/api/v1/auth/oidc/callback")
    async def oidc_callback(
        request: Request, st: Ctx, code: str | None = None, state: str | None = None, error: str | None = None
    ):
        saved = await st.oidc_states.pop(state) if state else None
        if error or not code or saved is None:
            LOGINS.labels("oidc", "failure").inc()
            return RedirectResponse("/login?error=sso", status_code=302)
        try:
            identity = await st.oidc.exchange(code, saved["verifier"], saved["nonce"])
        except OidcError as exc:
            log.warning("oidc callback rejected", extra={"fields": {"reason": str(exc)}})
            LOGINS.labels("oidc", "failure").inc()
            await st.repo.record_login("?", "oidc", False, client_ip(request))
            return RedirectResponse("/login?error=sso", status_code=302)

        return await start_sso_session(
            st,
            "oidc",
            identity.subject,
            identity.username,
            identity.display_name,
            identity.roles,
            client_ip(request),
            saved["next"],
        )

    # ---- SAML 2.0 SP -------------------------------------------------------------------

    @app.get("/api/v1/auth/saml/metadata")
    async def saml_metadata(st: Ctx):
        if not settings.saml_enabled:
            raise ApiError(404, "saml_disabled", "Вход через SAML выключен")
        return Response(st.saml.metadata(), media_type="application/samlmetadata+xml")

    @app.get("/api/v1/auth/saml/login")
    async def saml_login(st: Ctx, next: str = "/"):
        request_id = new_request_id()
        try:
            url = await st.saml.login_url(request_id)
        except SamlError as exc:
            raise ApiError(404, "saml_disabled", "Вход через SAML сейчас недоступен") from exc
        await st.saml_requests.put(request_id, {"next": safe_next(next)})
        return RedirectResponse(url, status_code=302)

    @app.post("/api/v1/auth/saml/acs")
    async def saml_acs(request: Request, st: Ctx):
        # IdP присылает форму со своего домена: Origin чужой, это нормально. Защищают подпись,
        # InResponseTo (ID нашего запроса, одноразовый) и запрет повторного использования ассерции.
        ip = client_ip(request)
        body = await request.body()
        fields = parse_qs(body.decode("latin-1"), max_num_fields=10) if len(body) <= 400_000 else {}
        saml_response = (fields.get("SAMLResponse") or [""])[0]

        def reject(reason: str) -> Response:
            log.warning("saml response rejected", extra={"fields": {"reason": reason}})
            LOGINS.labels("saml", "failure").inc()
            return RedirectResponse("/login?error=sso", status_code=303)

        if not saml_response:
            return reject("no SAMLResponse")
        try:
            assertion = await st.saml.parse(saml_response)
        except SamlError as exc:
            await st.repo.record_login("?", "saml", False, ip)
            return reject(str(exc))

        saved = await st.saml_requests.pop(assertion.in_response_to)
        if saved is None:
            return reject("unknown or reused InResponseTo")
        ttl = (assertion.expires_at - datetime.now(timezone.utc)).total_seconds() + settings.saml_clock_skew_seconds
        if not await st.saml_replay.first_use(assertion.assertion_id, int(ttl)):
            return reject("assertion replay")

        attrs = assertion.attributes
        roles = map_roles(
            settings,
            attrs.get(settings.saml_roles_attribute, []) if settings.saml_roles_attribute else [],
            attrs.get(settings.saml_groups_attribute, []) if settings.saml_groups_attribute else [],
        )
        username = assertion.first(*USERNAME_ATTRS) or assertion.subject
        display = assertion.first(*NAME_ATTRS) or username
        return await start_sso_session(
            st, "saml", assertion.subject, username[:128], display, roles, ip, saved["next"], 303
        )

    # ---- gateway hooks -----------------------------------------------------------------

    @app.get("/internal/verify")
    async def verify(request: Request, st: Ctx, redirect: int = 0):
        method = request.headers.get("x-forwarded-method", "GET").upper()
        uri = request.headers.get("x-forwarded-uri", "/")
        path = urlsplit(uri).path

        token, kind = extract_token(request)
        session = await st.sessions.get(token) if token else None
        if session is None:
            VERIFY.labels("unauthenticated").inc()
            if redirect:
                # Traefik resolves a relative Location against the auth address, so it must be absolute
                host = request.headers.get("x-forwarded-host")
                proto = request.headers.get("x-forwarded-proto", "http")
                base = f"{proto}://{host}" if host else settings.public_url.rstrip("/")
                return RedirectResponse(f"{base}/login?next={quote(uri, safe='')}", status_code=302)
            return error_response(401, "unauthorized", "Нужно войти в систему")

        if method in UNSAFE_METHODS:
            if kind == "cookie" and not same_origin(request):
                VERIFY.labels("csrf").inc()
                return error_response(403, "csrf_rejected", "Запрос пришёл с чужого сайта")
            if path.startswith(ADMIN_ONLY_PREFIXES) and "admin" not in session.roles:
                VERIFY.labels("forbidden").inc()
                return error_response(403, "forbidden", "Это действие доступно только администратору")

        VERIFY.labels("ok").inc()
        return Response(
            status_code=200,
            headers={
                "X-User-Id": session.user_id,
                "X-User-Name": session.username,
                "X-User-Roles": ",".join(session.roles),
                "X-Auth-Method": session.auth_method,
            },
        )

    @app.get("/internal/verify-metrics")
    async def verify_metrics(request: Request):
        auth = request.headers.get("authorization", "")
        supplied = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        if supplied and secrets.compare_digest(supplied, settings.metrics_token):
            return Response(status_code=200)
        return error_response(
            401, "unauthorized", "Нужен токен сборщика метрик", {"WWW-Authenticate": 'Bearer realm="metrics"'}
        )

    # ---- probes ------------------------------------------------------------------------

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(st: Ctx):
        try:
            await st.redis.ping()
            await st.repo.ping()
        except Exception as exc:  # noqa: BLE001
            return error_response(503, "not_ready", str(exc))
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics():
        return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

    return app
