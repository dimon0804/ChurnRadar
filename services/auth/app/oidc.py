from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from .config import Settings
from .roles import map_roles


class OidcError(Exception):
    pass


@dataclass
class OidcIdentity:
    subject: str
    username: str
    display_name: str
    roles: list[str]
    email: str | None = None


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class OidcClient:
    """Authorization Code + PKCE against any OIDC provider (Authentik, Keycloak, ...).

    Endpoints come from the discovery document. The browser talks to the public issuer, while
    discovery, token exchange and JWKS go over the internal network. Authentik builds every URL
    (including `iss` in tokens) from the Host header, so back-channel calls carry the public host.
    """

    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        self.public_issuer = settings.oidc_public_issuer
        self.internal_issuer = settings.oidc_internal_issuer or settings.oidc_public_issuer
        self.public_origin = _origin(self.public_issuer)
        self.internal_origin = _origin(self.internal_issuer)
        self._config: tuple[float, dict | None] = (0.0, None)
        self._jwks: tuple[float, jwt.PyJWKSet | None] = (0.0, None)

    @property
    def _headers(self) -> dict[str, str]:
        if self.public_origin == self.internal_origin:
            return {}
        return {"Host": urlsplit(self.public_issuer).netloc}

    def _backchannel(self, url: str) -> str:
        """Public endpoint URL -> the same endpoint on the internal address."""
        if url.startswith(self.public_origin + "/") and self.public_origin != self.internal_origin:
            return self.internal_origin + url[len(self.public_origin) :]
        return url

    def _front(self, url: str) -> str:
        if url.startswith(self.internal_origin + "/") and self.public_origin != self.internal_origin:
            return self.public_origin + url[len(self.internal_origin) :]
        return url

    async def _discovery(self) -> dict | None:
        fetched_at, config = self._config
        ttl = 300 if config else 15
        if time.monotonic() - fetched_at < ttl:
            return config
        try:
            resp = await self.http.get(
                self.internal_issuer.rstrip("/") + "/.well-known/openid-configuration",
                headers=self._headers,
                timeout=1.5,
            )
            data = resp.json() if resp.status_code == 200 else None
            if data and not all(k in data for k in ("authorization_endpoint", "token_endpoint", "jwks_uri")):
                data = None
        except (httpx.HTTPError, ValueError):
            data = None
        self._config = (time.monotonic(), data)
        return data

    async def available(self) -> bool:
        if not self.s.oidc_enabled:
            return False
        return await self._discovery() is not None

    async def authorize_url(self, state: str, nonce: str, challenge: str) -> str:
        config = await self._discovery()
        if config is None:
            raise OidcError("provider unavailable")
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.s.oidc_client_id,
                "redirect_uri": self.s.oidc_redirect_uri,
                "scope": self.s.oidc_scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        endpoint = self._front(config["authorization_endpoint"])
        return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"

    async def _jwk_set(self, force: bool = False) -> jwt.PyJWKSet:
        fetched_at, jwks = self._jwks
        if jwks is None or force or time.monotonic() - fetched_at > 300:
            config = await self._discovery()
            if config is None:
                raise OidcError("provider unavailable")
            resp = await self.http.get(self._backchannel(config["jwks_uri"]), headers=self._headers, timeout=5)
            resp.raise_for_status()
            jwks = jwt.PyJWKSet.from_dict(resp.json())
            self._jwks = (time.monotonic(), jwks)
        return jwks

    async def _signing_key(self, token: str):
        kid = jwt.get_unverified_header(token).get("kid")
        for force in (False, True):
            try:
                keys = (await self._jwk_set(force)).keys
            except (httpx.HTTPError, jwt.PyJWTError, ValueError) as exc:
                raise OidcError(f"jwks unavailable: {exc}") from exc
            for key in keys:
                if key.key_id == kid:
                    return key
        raise OidcError("unknown signing key")

    async def exchange(self, code: str, verifier: str, nonce: str) -> OidcIdentity:
        config = await self._discovery()
        if config is None:
            raise OidcError("provider unavailable")
        try:
            resp = await self.http.post(
                self._backchannel(config["token_endpoint"]),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.s.oidc_redirect_uri,
                    "client_id": self.s.oidc_client_id,
                    "client_secret": self.s.oidc_client_secret,
                    "code_verifier": verifier,
                },
                headers=self._headers,
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
        # Authentik пишет issuer со слешем на конце, Keycloak — без; в настройках может быть любой вариант
        issuers = list(
            dict.fromkeys([self.public_issuer, self.public_issuer.rstrip("/"), self.public_issuer.rstrip("/") + "/"])
        )
        try:
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256", "ES256", "PS256"],
                audience=self.s.oidc_client_id,
                issuer=issuers,
                leeway=30,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise OidcError(f"invalid id_token: {exc}") from exc
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OidcError("nonce mismatch")
        return identity_from_claims(claims, self.s)


def identity_from_claims(claims: dict, settings: Settings) -> OidcIdentity:
    raw_roles = list(claims.get("roles") or [])
    raw_roles += list((claims.get("realm_access") or {}).get("roles") or [])
    groups = claims.get("groups") or []
    if isinstance(groups, str):
        groups = [groups]
    roles = map_roles(settings, raw_roles, groups)
    username = claims.get("preferred_username") or claims.get("email") or claims["sub"]
    display = claims.get("name") or username
    email = claims.get("email")
    return OidcIdentity(
        subject=claims["sub"], username=username, display_name=display, roles=roles, email=email or None
    )
