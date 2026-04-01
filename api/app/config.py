"""Application configuration via pydantic-settings.

ARCH-OVERVIEW §8.1: All environment variables validated at import time.
A Settings instance is created once — all layers receive it via constructor
injection or Depends(), never via global import in production code.
"""

import logging
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Environment-driven application configuration."""

    # ── Database ─────────────────────────────────────────────
    DATABASE_URL: str

    # ── Parcel API ───────────────────────────────────────────
    PARCEL_API_KEY: str

    # ── Authentication ───────────────────────────────────────
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    BCRYPT_ROUNDS: int = 12
    COOKIE_SECURE: bool = False
    TRUST_PROXY_HEADERS: bool = True

    # ── First-run seeding (optional after first start) ───────
    ADMIN_USERNAME: str | None = None
    ADMIN_PASSWORD: str | None = None

    # ── Polling ──────────────────────────────────────────────
    POLL_INTERVAL_MINUTES: int = 15
    POLL_JITTER_SECONDS: int = 30
    POLL_HTTP_TIMEOUT_SECONDS: int = 30
    POLL_MAX_RETRIES: int = 3

    # ── Application ──────────────────────────────────────────
    ENVIRONMENT: Literal["development", "production"] = "production"
    LOG_LEVEL: str = "INFO"

    # ── Frontend / HTTPS ─────────────────────────────────────
    HTTPS_ENABLED: bool = False

    model_config = {"env_file": ".env", "extra": "ignore"}

    # ── Validators ───────────────────────────────────────────

    @field_validator("PARCEL_API_KEY")
    @classmethod
    def parcel_api_key_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("PARCEL_API_KEY must be a non-empty string")
        return v

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def jwt_secret_min_length(cls, v: str) -> str:
        if len(v) < 32:
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
        if v < 5:
            raise ValueError("POLL_INTERVAL_MINUTES must be at least 5")
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


def _warn_https_cookie_mismatch(s: Settings) -> None:
    if s.HTTPS_ENABLED and not s.COOKIE_SECURE:
        logger.warning(
            "HTTPS_ENABLED=true but COOKIE_SECURE=false — "
            "cookies will not have the Secure flag set"
        )


# Module-level singleton — validated at import time
settings = Settings()  # type: ignore[call-arg]
_warn_https_cookie_mismatch(settings)
