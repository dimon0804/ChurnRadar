from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.oidc import identity_from_claims

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-key"
PUBLIC = "http://localhost:9000"
INTERNAL = "http://authentik-server:9000"


def jwks() -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(PRIVATE_KEY.public_key()))
    jwk.update(kid=KID, use="sig", alg="RS256")
    return {"keys": [jwk]}


class FakeAuthentik:
    """Authentik-like provider: URLs and `iss` are built from the Host header of each request."""

    def __init__(self, settings: Settings):
        self.s = settings
        self.nonce = None
        self.claims: dict = {"groups": ["churn-admins"]}
        self.last_token_form: dict = {}
        self.requests: list[httpx.Request] = []

    def base(self, request: httpx.Request) -> str:
        return f"http://{request.headers['host']}"

    def id_token(self, request: httpx.Request) -> str:
        claims = {
            "iss": self.base(request) + "/application/o/churn-radar/",
            "aud": self.s.oidc_client_id,
            "sub": "ak-user-1",
            "preferred_username": "marketer",
            "name": "Мария Маркетолог",
            "nonce": self.nonce,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            **self.claims,
        }
        return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        base = self.base(request)
        if path == "/application/o/churn-radar/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": base + "/application/o/churn-radar/",
                    "authorization_endpoint": base + "/application/o/authorize/",
                    "token_endpoint": base + "/application/o/token/",
                    "jwks_uri": base + "/application/o/churn-radar/jwks/",
                },
            )
        if path == "/application/o/churn-radar/jwks/":
            return httpx.Response(200, json=jwks())
        if path == "/application/o/token/":
            self.last_token_form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            return httpx.Response(200, json={"id_token": self.id_token(request), "access_token": "x"})
        return httpx.Response(404)


@pytest.fixture
def settings() -> Settings:
    return Settings(oidc_enabled=True, saml_enabled=False, log_level="warning")


@pytest.fixture
def idp(settings):
    return FakeAuthentik(settings)


@pytest.fixture
def upstream_transport(idp):
    return httpx.MockTransport(idp)


async def start_flow(client, idp, next_url="/customers"):
    resp = await client.get("/api/v1/auth/oidc/login", params={"next": next_url})
    assert resp.status_code == 302
    location = urlsplit(resp.headers["location"])
    query = {k: v[0] for k, v in parse_qs(location.query).items()}
    idp.nonce = query["nonce"]
    return location, query


async def test_providers_reports_sso(client):
    body = (await client.get("/api/v1/auth/providers")).json()
    assert body["oidc"] == {
        "enabled": True,
        "display_name": "Войти через Authentik (OIDC)",
        "login_url": "/api/v1/auth/oidc/login",
    }
    assert body["saml"] == {"enabled": False}


async def test_full_code_flow_with_pkce(client, idp, settings):
    location, query = await start_flow(client, idp)
    # браузер уходит на публичный адрес, хотя discovery скачан по внутреннему
    assert f"{location.scheme}://{location.netloc}{location.path}" == PUBLIC + "/application/o/authorize/"
    assert query["code_challenge_method"] == "S256"
    assert query["scope"] == settings.oidc_scopes
    assert query["redirect_uri"] == "http://localhost:8080/api/v1/auth/oidc/callback"

    cb = await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    assert cb.status_code == 302
    assert cb.headers["location"] == "/customers"
    assert idp.last_token_form["code_verifier"]
    assert idp.last_token_form["client_secret"] == settings.oidc_client_secret

    # обмен кода и JWKS идут по внутренней сети, но с публичным Host — иначе iss не совпадёт
    backchannel = [r for r in idp.requests if r.url.path != "/application/o/authorize/"]
    assert {str(r.url.host) for r in backchannel} == {"authentik-server"}
    assert {r.headers["host"] for r in backchannel} == {"localhost:9000"}

    me = (await client.get("/api/v1/auth/me")).json()
    assert me["auth_method"] == "oidc"
    assert me["username"] == "marketer"
    assert me["roles"] == ["admin"]


async def test_roles_claim_from_scope_mapping(client, idp):
    idp.claims = {"roles": ["analyst"], "groups": []}
    _, query = await start_flow(client, idp)
    await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    assert (await client.get("/api/v1/auth/me")).json()["roles"] == ["analyst"]


async def test_user_without_groups_is_refused(client, idp, repo):
    idp.claims = {"groups": ["staff"]}
    _, query = await start_flow(client, idp)
    cb = await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    assert cb.headers["location"] == "/login?error=forbidden"
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert repo.logins[-1][1:3] == ("oidc", False)


async def test_state_is_single_use(client, idp):
    _, query = await start_flow(client, idp)
    await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    client.cookies.clear()
    replay = await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    assert replay.headers["location"] == "/login?error=sso"


async def test_nonce_mismatch_rejected(client, idp):
    _, query = await start_flow(client, idp)
    idp.nonce = "forged"
    cb = await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    assert cb.headers["location"] == "/login?error=sso"
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_foreign_issuer_rejected(client, idp):
    _, query = await start_flow(client, idp)
    idp.claims = {"groups": ["churn-admins"], "iss": "http://evil.example/application/o/churn-radar/"}
    cb = await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    assert cb.headers["location"] == "/login?error=sso"


async def test_open_redirect_blocked(client, idp):
    _, query = await start_flow(client, idp, next_url="//evil.example/x")
    cb = await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    assert cb.headers["location"] == "/"


async def test_provider_error_redirects_to_login(client):
    cb = await client.get("/api/v1/auth/oidc/callback", params={"error": "access_denied", "state": "x"})
    assert cb.headers["location"] == "/login?error=sso"


def test_roles_mapping():
    s = Settings(oidc_enabled=False, saml_enabled=False)
    ident = identity_from_claims({"sub": "1", "preferred_username": "m", "realm_access": {"roles": ["offline"]}}, s)
    assert ident.roles == []
    ident = identity_from_claims({"sub": "1", "realm_access": {"roles": ["admin", "analyst"]}}, s)
    assert ident.roles == ["admin", "analyst"]
    assert ident.username == "1"
    # Keycloak отдаёт группы путями
    assert identity_from_claims({"sub": "1", "groups": ["/churn-analysts"]}, s).roles == ["analyst"]
    # роль по умолчанию теперь в настройках входа (её применяет sso_sign_in), не в разборе claims
    lenient = Settings(oidc_enabled=False, saml_enabled=False, sso_default_role="analyst")
    assert identity_from_claims({"sub": "1", "groups": []}, lenient).roles == []
