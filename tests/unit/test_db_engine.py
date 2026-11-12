"""Engine configuration. No connection is made."""

from __future__ import annotations

import pytest

from app import db
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _clean_engine():
    db.dispose_engine()
    yield
    db.dispose_engine()


def test_engine_is_lazy_and_cached() -> None:
    assert db._engine is None
    first = db.get_engine()
    assert db.get_engine() is first


def test_pool_follows_settings() -> None:
    settings = get_settings()
    engine = db.build_engine()
    assert engine.pool.size() == settings.db_pool_size
    assert engine.pool._max_overflow == settings.db_max_overflow
    engine.dispose()


def test_engine_uses_psycopg_and_environment_credentials() -> None:
    settings = get_settings()
    engine = db.build_engine()
    assert engine.url.drivername == "postgresql+psycopg"
    assert engine.url.password == settings.postgres_password.get_secret_value()
    engine.dispose()


def test_init_engine_replaces_the_previous_engine() -> None:
    first = db.init_engine()
    second = db.init_engine()
    assert first is not second
    assert db.get_engine() is second


def test_check_connection_is_false_without_a_database() -> None:
    assert db.check_connection() is False
