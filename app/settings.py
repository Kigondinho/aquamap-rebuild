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

    # Optional email second factor
    enable_2fa: bool = False
    otp_ttl_minutes: int = 5
    otp_max_attempts: int = 5
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None

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
        "session_ttl_hours", "otp_ttl_minutes", "otp_max_attempts",
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
        return self

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
