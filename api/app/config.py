"""Application configuration via pydantic-settings.

ARCH-OVERVIEW §8.1: All environment variables validated at import time.
A Settings instance is created once — all layers receive it via constructor
injection or Depends(), never via global import in production code.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Environment-driven application configuration.

    Required fields (startup fails if absent — SEC-REQ-047):
      PARCEL_API_KEY, DATABASE_URL, JWT_SECRET_KEY

    All SecretStr fields are never logged or exposed in tracebacks.
    """

    # ── Database ─────────────────────────────────────────────
    DATABASE_URL: str
    """Async PostgreSQL URL:  postgresql+psycopg://user:pass@host:5432/db"""

    # ── Parcel API ───────────────────────────────────────────
    PARCEL_API_KEY: Optional[SecretStr] = None
    """API key for api.parcel.app — required unless DEMO_MODE=true."""

    # ── Authentication ───────────────────────────────────────
    JWT_SECRET_KEY: SecretStr
    """HS256 signing key — must be ≥ 32 characters (SEC-REQ-010, SEC-REQ-048)."""

    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    """Valid range: 5–1440 minutes (SEC-REQ-014)."""
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    """Valid range: 1–30 days (SEC-REQ-014)."""

    BCRYPT_ROUNDS: int = 12
    """bcrypt cost factor — valid range: 10–15 (SEC-REQ-002)."""
    COOKIE_SECURE: bool = False
    """Set True when running behind HTTPS (SEC-REQ-023)."""
    TRUST_PROXY_HEADERS: bool = True
    """Read X-Real-IP / X-Forwarded-For for rate limiting (SEC-REQ-040)."""

    # ── First-run seeding (optional after first start) ───────
    ADMIN_USERNAME: Optional[str] = None
    ADMIN_PASSWORD: Optional[SecretStr] = None

    # ── Polling ──────────────────────────────────────────────
    POLL_INTERVAL_MINUTES: int = 15
    """Poll cadence in minutes — values < 5 are clamped to 5 (POLL-REQ-005)."""
    POLL_JITTER_SECONDS: int = 30
    """Maximum random jitter added to each interval (±N seconds)."""
    POLL_HTTP_TIMEOUT_SECONDS: int = 30
    POLL_MAX_RETRIES: int = 3

    # ── Application ──────────────────────────────────────────
    ENVIRONMENT: Literal["development", "production"] = "production"
    """'development' enables CORS and OpenAPI docs."""
    LOG_LEVEL: str = "INFO"
    VERSION: str = "1.0.0"

    # ── Frontend / HTTPS ─────────────────────────────────────
    HTTPS_ENABLED: bool = False
    FRONTEND_HTTP_PORT: int = 80

    DEMO_MODE: bool = False
    """When True: seed demo user + fixtures, skip polling, PARCEL_API_KEY not required."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Validators ───────────────────────────────────────────

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def jwt_secret_min_length(cls, v: SecretStr) -> SecretStr:
        """JWT secret must be at least 32 characters (SEC-REQ-010, SEC-REQ-048)."""
        if len(v.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return v

    @field_validator("ACCESS_TOKEN_EXPIRE_MINUTES")
    @classmethod
    def access_token_range(cls, v: int) -> int:
        if not 5 <= v <= 1440:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES must be between 5 and 1440")
        return v

    @field_validator("REFRESH_TOKEN_EXPIRE_DAYS")
    @classmethod
    def refresh_token_range(cls, v: int) -> int:
        if not 1 <= v <= 30:
            raise ValueError("REFRESH_TOKEN_EXPIRE_DAYS must be between 1 and 30")
        return v

    @field_validator("POLL_INTERVAL_MINUTES")
    @classmethod
    def poll_interval_minimum(cls, v: int) -> int:
        """Values below 5 are clamped to 5 with a warning (POLL-REQ-005).

        A clamp (not rejection) prevents startup failures when the operator
        sets an aggressive interval during development.
        """
        if v < 5:
            logger.warning(
                "POLL_INTERVAL_MINUTES=%d is below the minimum of 5; clamping to 5",
                v,
            )
            return 5
        return v

    @field_validator("BCRYPT_ROUNDS")
    @classmethod
    def bcrypt_rounds_range(cls, v: int) -> int:
        if not 10 <= v <= 15:
            raise ValueError("BCRYPT_ROUNDS must be between 10 and 15")
        return v

    @field_validator("POLL_JITTER_SECONDS")
    @classmethod
    def poll_jitter_range(cls, v: int) -> int:
        if not 0 <= v <= 120:
            raise ValueError("POLL_JITTER_SECONDS must be between 0 and 120")
        return v

    @field_validator("POLL_HTTP_TIMEOUT_SECONDS")
    @classmethod
    def poll_timeout_range(cls, v: int) -> int:
        if not 5 <= v <= 120:
            raise ValueError("POLL_HTTP_TIMEOUT_SECONDS must be between 5 and 120")
        return v

    @field_validator("POLL_MAX_RETRIES")
    @classmethod
    def poll_retries_range(cls, v: int) -> int:
        if not 0 <= v <= 5:
            raise ValueError("POLL_MAX_RETRIES must be between 0 and 5")
        return v

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def normalise_environment(cls, v: str) -> str:
        """Normalise to lowercase so ENVIRONMENT=PRODUCTION works too."""
        return str(v).lower()

    @model_validator(mode="after")
    def require_parcel_api_key_when_not_demo(self) -> "Settings":
        """PARCEL_API_KEY is required unless DEMO_MODE=true."""
        if not self.DEMO_MODE:
            if self.PARCEL_API_KEY is None or not self.PARCEL_API_KEY.get_secret_value().strip():
                raise ValueError(
                    "PARCEL_API_KEY is required when DEMO_MODE is not enabled"
                )
        return self

    # ── Computed properties ───────────────────────────────────

    @property
    def sync_database_url(self) -> str:
        """Synchronous database URL for Alembic migrations.

        Replaces the async ``postgresql+psycopg://`` driver prefix with the
        synchronous ``postgresql+psycopg2://`` prefix required by Alembic's
        ``env.py``.  If the URL already uses psycopg2, it is returned unchanged.
        """
        return self.DATABASE_URL.replace(
            "postgresql+psycopg://", "postgresql+psycopg2://", 1
        )


def _warn_https_cookie_mismatch(s: Settings) -> None:
    """Log a warning when HTTPS is enabled but cookie Secure flag is off."""
    if s.HTTPS_ENABLED and not s.COOKIE_SECURE:
        logger.warning(
            "HTTPS_ENABLED=true but COOKIE_SECURE=false — "
            "cookies will not have the Secure flag set (SEC-REQ-044)"
        )


# Module-level singleton — validated at import time (SEC-REQ-047).
# All layers that need settings should receive this instance via DI or
# explicit parameter — never re-instantiate Settings().
settings = Settings()  # type: ignore[call-arg]
_warn_https_cookie_mismatch(settings)
