from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import Settings

GOOD_KEY = "k" * 40


def _settings(**overrides: str) -> Settings:
    values = {"secret_key": GOOD_KEY, "postgres_password": "pw", **overrides}
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_secret_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_KEY")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_short_secret_key_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key="too-short")


def test_placeholder_secret_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        _settings(app_env="production", secret_key="change-me" + "x" * 30)


def test_placeholder_database_password_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        _settings(app_env="production", postgres_password="change-me")


def test_production_flag_and_defaults() -> None:
    assert _settings(app_env="production").is_production
    assert not _settings().is_production
    assert _settings().enable_2fa is False


def test_database_url_uses_psycopg_and_hides_password_in_repr() -> None:
    settings = _settings(postgres_host="db", postgres_port="5432", postgres_db="x", postgres_user="u")
    url = settings.database_url
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database, url.username) == ("db", 5432, "x", "u")
    assert "pw" not in repr(settings)
    assert "pw" not in str(url)  # SQLAlchemy masks the password by default


def test_pool_defaults_and_validation() -> None:
    settings = _settings()
    assert (settings.db_pool_size, settings.db_max_overflow, settings.db_connect_timeout) == (5, 10, 3)
    with pytest.raises(ValidationError):
        _settings(db_pool_size="0")
