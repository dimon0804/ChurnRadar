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


def jwks() -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(PRIVATE_KEY.public_key()))
    jwk.update(kid=KID, use="sig", alg="RS256")
    return {"keys": [jwk]}


class FakeKeycloak:
    def __init__(self, settings: Settings):
        self.s = settings
        self.nonce = None
        self.roles = ["admin"]
        self.last_token_form: dict = {}

    def id_token(self) -> str:
        claims = {
            "iss": self.s.oidc_public_issuer,
            "aud": self.s.oidc_client_id,
            "sub": "kc-user-1",
            "preferred_username": "marketer",
            "name": "Мария Маркетолог",
            "roles": self.roles,
            "nonce": self.nonce,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        }
        return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"issuer": self.s.oidc_public_issuer})
        if path.endswith("/certs"):
            return httpx.Response(200, json=jwks())
        if path.endswith("/token"):
            self.last_token_form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            return httpx.Response(200, json={"id_token": self.id_token(), "access_token": "x"})
        return httpx.Response(404)


@pytest.fixture
def settings() -> Settings:
    return Settings(oidc_enabled=True, log_level="warning")


@pytest.fixture
def keycloak(settings):
    return FakeKeycloak(settings)


@pytest.fixture
def upstream_transport(keycloak):
    return httpx.MockTransport(keycloak)


async def start_flow(client, keycloak, next_url="/customers"):
    resp = await client.get("/api/v1/auth/oidc/login", params={"next": next_url})
    assert resp.status_code == 302
    location = urlsplit(resp.headers["location"])
    query = {k: v[0] for k, v in parse_qs(location.query).items()}
    keycloak.nonce = query["nonce"]
    return location, query


async def test_providers_reports_sso(client):
    body = (await client.get("/api/v1/auth/providers")).json()
    assert body["oidc"] == {
        "enabled": True,
        "display_name": "Войти через SSO",
        "login_url": "/api/v1/auth/oidc/login",
    }


async def test_full_code_flow_with_pkce(client, keycloak, settings):
    location, query = await start_flow(client, keycloak)
    assert f"{location.scheme}://{location.netloc}" == "http://localhost:8081"
    assert query["code_challenge_method"] == "S256"
    assert query["redirect_uri"] == "http://localhost:8080/api/v1/auth/oidc/callback"

    cb = await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    assert cb.status_code == 302
    assert cb.headers["location"] == "/customers"
    assert keycloak.last_token_form["code_verifier"]
    assert keycloak.last_token_form["client_secret"] == settings.oidc_client_secret

    me = (await client.get("/api/v1/auth/me")).json()
    assert me["auth_method"] == "oidc"
    assert me["username"] == "marketer"
    assert me["roles"] == ["admin"]


async def test_state_is_single_use(client, keycloak):
    _, query = await start_flow(client, keycloak)
    await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    client.cookies.clear()
    replay = await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    assert replay.headers["location"] == "/login?error=sso"


async def test_nonce_mismatch_rejected(client, keycloak):
    _, query = await start_flow(client, keycloak)
    keycloak.nonce = "forged"
    cb = await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    assert cb.headers["location"] == "/login?error=sso"
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_open_redirect_blocked(client, keycloak):
    _, query = await start_flow(client, keycloak, next_url="//evil.example/x")
    cb = await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})
    assert cb.headers["location"] == "/"


async def test_provider_error_redirects_to_login(client):
    cb = await client.get("/api/v1/auth/oidc/callback", params={"error": "access_denied", "state": "x"})
    assert cb.headers["location"] == "/login?error=sso"


def test_roles_mapping_defaults_to_analyst():
    ident = identity_from_claims({"sub": "1", "preferred_username": "m", "realm_access": {"roles": ["offline"]}})
    assert ident.roles == ["analyst"]
    ident = identity_from_claims({"sub": "1", "realm_access": {"roles": ["admin", "analyst"]}})
    assert ident.roles == ["admin", "analyst"]
    assert ident.username == "1"
