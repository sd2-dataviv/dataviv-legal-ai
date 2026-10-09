"""Application settings, read from environment variables (prefix DATAVIV_) or backend/.env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATAVIV_", env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore"
    )

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    app_name: str = "Dataviv AI OS"

    # The API connects as app_user (RLS applies). Migrations and seeding use the owner URL.
    database_url: str = "postgresql+asyncpg://app_user:app_user_dev_only@localhost:5432/dataviv"
    database_owner_url: str = "postgresql+asyncpg://dataviv:dataviv_dev_only@localhost:5432/dataviv"

    # Auth. Dev/test: HS256 tokens signed with jwt_secret. Staging/prod: OIDC (Keycloak) via JWKS.
    jwt_secret: str = "change-me-dev-only-secret-32-bytes-min"  # noqa: S105 - dev default, overridden in prod
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60 * 8
    oidc_issuer: str | None = None
    oidc_audience: str | None = None

    # Model gateway (Step 1+). "fake" = deterministic offline model for dev and tests.
    llm_backend: Literal["fake", "litellm"] = "fake"
    litellm_base_url: str = "http://localhost:4000"

    cors_origins: list[str] = ["http://localhost:3000"]

    @property
    def dev_auth_enabled(self) -> bool:
        return self.env in ("dev", "test")


@lru_cache
def get_settings() -> Settings:
    return Settings()
