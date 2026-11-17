"""Environment-driven configuration.

Every value comes from environment variables or an optional ``.env`` file. There
are no secrets in source code. ``SECRET_KEY`` and ``POSTGRES_PASSWORD`` have no
defaults, so the application refuses to start without them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

_PLACEHOLDER_PREFIX = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "AquaMap"
    secret_key: SecretStr
    session_cookie_name: str = "aquamap_session"
    session_ttl_hours: int = 12
    # Secure flag of the session cookie. Unset = on in production, off otherwise (plain http://localhost).
    session_cookie_secure: bool | None = None
    session_cookie_samesite: Literal["lax", "strict"] = "lax"

    # Optional email second factor at sign-in (not built; leave off)
    enable_2fa: bool = False

    # One-time codes (email verification and password reset)
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    otp_resend_seconds: int = 60

    # Email. EMAIL_BACKEND: auto | smtp | console | none
    #   auto    -> smtp when SMTP_HOST is set; otherwise console in development/test and none in production
    #   console -> the message (including the code) is written to the server log; refused in production
    #   none    -> no email: accounts need no verification and password reset by email is unavailable
    email_backend: Literal["auto", "smtp", "console", "none"] = "auto"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    smtp_timeout: int = 10

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "aquamap_db"
    postgres_user: str = "aquamap_user"
    postgres_password: SecretStr

    # Connection pool and timeouts (seconds unless noted)
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    db_connect_timeout: int = 3
    db_echo: bool = False

    # Data
    data_dir: Path = Path("data")

    @field_validator("secret_key")
    @classmethod
    def _secret_key_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        return value

    @field_validator(
        "session_ttl_hours", "otp_ttl_minutes", "otp_max_attempts", "smtp_timeout",
        "db_pool_size", "db_pool_timeout", "db_pool_recycle", "db_connect_timeout",
    )
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be at least 1")
        return value

    @model_validator(mode="after")
    def _reject_placeholders_in_production(self) -> "Settings":
        if self.app_env == "production":
            if self.secret_key.get_secret_value().startswith(_PLACEHOLDER_PREFIX):
                raise ValueError("SECRET_KEY still has the placeholder value")
            if self.postgres_password.get_secret_value().startswith(_PLACEHOLDER_PREFIX):
                raise ValueError("POSTGRES_PASSWORD still has the placeholder value")
            if self.email_backend == "console":
                raise ValueError("EMAIL_BACKEND=console writes one-time codes to the log; not allowed in production")
            if self.session_cookie_secure is False:
                raise ValueError("SESSION_COOKIE_SECURE=false is not allowed in production")
        if self.email_backend == "smtp" and not (self.smtp_host and self.smtp_from):
            raise ValueError("EMAIL_BACKEND=smtp needs SMTP_HOST and SMTP_FROM")
        if self.otp_resend_seconds < 0:
            raise ValueError("OTP_RESEND_SECONDS must not be negative")
        return self

    @field_validator("smtp_host", "smtp_user", "smtp_from", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def resolved_email_backend(self) -> Literal["smtp", "console", "none"]:
        if self.email_backend != "auto":
            return self.email_backend
        if self.smtp_host and self.smtp_from:
            return "smtp"
        return "none" if self.is_production else "console"

    @property
    def email_available(self) -> bool:
        """True when the server can send email, so verification and reset by email are switched on."""
        return self.resolved_email_backend != "none"

    @property
    def cookie_secure(self) -> bool:
        return self.is_production if self.session_cookie_secure is None else self.session_cookie_secure

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def database_url(self) -> URL:
        """SQLAlchemy URL (psycopg 3 driver). Kept as an object so the password is not logged."""
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance. Tests call ``get_settings.cache_clear()`` after changing env."""
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
