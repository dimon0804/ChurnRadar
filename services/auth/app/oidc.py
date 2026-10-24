from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

from .config import Settings
from .users import VALID_ROLES


class OidcError(Exception):
    pass


@dataclass
class OidcIdentity:
    subject: str
    username: str
    display_name: str
    roles: list[str]


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class OidcClient:
    """Authorization Code + PKCE against Keycloak.

    The browser talks to the public issuer URL, while token exchange and JWKS go over
    the internal network, so the two base URLs are configured separately.
    """

    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        self._available: tuple[float, bool] = (0.0, False)
        self._jwks: tuple[float, jwt.PyJWKSet | None] = (0.0, None)

    @property
    def _oidc_path(self) -> str:
        return "/protocol/openid-connect"

    async def available(self) -> bool:
        if not self.s.oidc_enabled:
            return False
        checked_at, ok = self._available
        if time.monotonic() - checked_at < 15:
            return ok
        try:
            resp = await self.http.get(self.s.oidc_internal_issuer + "/.well-known/openid-configuration", timeout=1.5)
            ok = resp.status_code == 200
        except httpx.HTTPError:
            ok = False
        self._available = (time.monotonic(), ok)
        return ok

    def authorize_url(self, state: str, nonce: str, challenge: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.s.oidc_client_id,
                "redirect_uri": self.s.oidc_redirect_uri,
                "scope": "openid profile email",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.s.oidc_public_issuer}{self._oidc_path}/auth?{query}"

    async def _jwk_set(self, force: bool = False) -> jwt.PyJWKSet:
        fetched_at, jwks = self._jwks
        if jwks is None or force or time.monotonic() - fetched_at > 300:
            resp = await self.http.get(self.s.oidc_internal_issuer + self._oidc_path + "/certs", timeout=5)
            resp.raise_for_status()
            jwks = jwt.PyJWKSet.from_dict(resp.json())
            self._jwks = (time.monotonic(), jwks)
        return jwks

    async def _signing_key(self, token: str):
        kid = jwt.get_unverified_header(token).get("kid")
        for force in (False, True):
            for key in (await self._jwk_set(force)).keys:
                if key.key_id == kid:
                    return key
        raise OidcError("unknown signing key")

    async def exchange(self, code: str, verifier: str, nonce: str) -> OidcIdentity:
        try:
            resp = await self.http.post(
                self.s.oidc_internal_issuer + self._oidc_path + "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.s.oidc_redirect_uri,
                    "client_id": self.s.oidc_client_id,
                    "client_secret": self.s.oidc_client_secret,
                    "code_verifier": verifier,
                },
                timeout=5,
            )
        except httpx.HTTPError as exc:
            raise OidcError(f"token endpoint unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise OidcError(f"token exchange failed: {resp.status_code}")
        id_token = resp.json().get("id_token")
        if not id_token:
            raise OidcError("no id_token in response")

        key = await self._signing_key(id_token)
        try:
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256", "ES256", "PS256"],
                audience=self.s.oidc_client_id,
                issuer=self.s.oidc_public_issuer,
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise OidcError(f"invalid id_token: {exc}") from exc
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OidcError("nonce mismatch")
        return identity_from_claims(claims)


def identity_from_claims(claims: dict) -> OidcIdentity:
    raw_roles = set(claims.get("roles") or [])
    raw_roles |= set((claims.get("realm_access") or {}).get("roles") or [])
    roles = [r for r in VALID_ROLES if r in raw_roles] or ["analyst"]
    username = claims.get("preferred_username") or claims.get("email") or claims["sub"]
    display = claims.get("name") or username
    return OidcIdentity(subject=claims["sub"], username=username, display_name=display, roles=roles)
