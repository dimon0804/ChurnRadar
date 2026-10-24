from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "postgresql://churn:churn@postgres:5432/churn"
    redis_url: str = "redis://redis:6379/0"

    public_url: str = "http://localhost:8080"
    cookie_name: str = "cr_session"
    cookie_secure: bool = False
    session_ttl_seconds: int = 12 * 3600

    admin_username: str = "admin"
    admin_password: str = "admin"
    marketer_username: str = "marketer"
    marketer_password: str = "marketer"
    analyst_username: str = "analyst"
    analyst_password: str = "analyst"

    metrics_token: str = "churn-metrics-demo-token"

    login_max_attempts: int = 5
    login_window_seconds: int = 60

    # OIDC (Authorization Code + PKCE). Браузер ходит на публичный issuer, обмен кода и JWKS —
    # по внутреннему адресу; пустой internal = тот же, что публичный.
    oidc_enabled: bool = True
    oidc_public_issuer: str = "http://localhost:9000/application/o/churn-radar/"
    oidc_internal_issuer: str = "http://authentik-server:9000/application/o/churn-radar/"
    oidc_client_id: str = "churn-radar"
    oidc_client_secret: str = "churn-radar-demo-secret"
    oidc_scopes: str = "openid profile email"
    oidc_display_name: str = "Войти через Authentik (OIDC)"

    # SAML 2.0 SP. Метаданные IdP: URL, файл или XML прямо в переменной (что задано первым из
    # xml → file → url). public_url IdP нужен, когда метаданные качаются по внутреннему адресу.
    saml_enabled: bool = True
    saml_display_name: str = "Войти через Authentik (SAML)"
    saml_idp_metadata_url: str = "http://authentik-server:9000/application/saml/churn-radar-saml/metadata/"
    saml_idp_metadata_file: str = ""
    saml_idp_metadata_xml: str = ""
    saml_idp_public_url: str = "http://localhost:9000"
    saml_sp_entity_id: str = ""
    saml_roles_attribute: str = "churn-radar-role"
    saml_groups_attribute: str = "http://schemas.xmlsoap.org/claims/Group"
    saml_clock_skew_seconds: int = 90

    # роли из групп IdP (через запятую). Пустая роль по умолчанию — без групп в панель не пускаем.
    sso_admin_groups: str = "churn-admins"
    sso_marketer_groups: str = "churn-marketers"
    sso_analyst_groups: str = "churn-analysts"
    sso_default_role: str = ""

    log_level: str = "info"

    @property
    def asyncpg_dsn(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url

    @property
    def oidc_redirect_uri(self) -> str:
        return self.public_url.rstrip("/") + "/api/v1/auth/oidc/callback"

    @property
    def saml_acs_url(self) -> str:
        return self.public_url.rstrip("/") + "/api/v1/auth/saml/acs"

    @property
    def saml_entity_id(self) -> str:
        return self.saml_sp_entity_id or self.public_url.rstrip("/") + "/api/v1/auth/saml/metadata"


@lru_cache
def get_settings() -> Settings:
    return Settings()
