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
    analyst_username: str = "analyst"
    analyst_password: str = "analyst"

    metrics_token: str = "churn-metrics-demo-token"

    login_max_attempts: int = 5
    login_window_seconds: int = 60

    oidc_enabled: bool = True
    oidc_public_issuer: str = "http://localhost:8081/realms/churn-radar"
    oidc_internal_issuer: str = "http://keycloak:8080/realms/churn-radar"
    oidc_client_id: str = "churn-radar"
    oidc_client_secret: str = "churn-radar-demo-secret"
    oidc_display_name: str = "Войти через SSO"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
