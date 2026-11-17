"""Shared test setup.

Environment variables are set *before* the application is imported so tests never depend on a
developer's real ``.env``. The throw-away password is generated per run, and the database host
points at a closed port, so no test can reach the development database through the application's
settings. Tests that need a database are marked ``db`` and use the separate test database instead
(``pytest --test-db``; rules in ``tests/testdb.py``). Options and the end-of-run report: ``conftest.py`` at the
project root and ``tests/report.py``.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator

TEST_ENV = {
    "APP_ENV": "test",
    "SECRET_KEY": secrets.token_urlsafe(48),
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "1",
    "POSTGRES_DB": "aquamap_test",
    "POSTGRES_USER": "aquamap_test",
    "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
    "ENABLE_2FA": "false",
    "EMAIL_BACKEND": "none",  # tests that need email switch to the console backend themselves
}
os.environ.update(TEST_ENV)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, create_engine  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402



from app.db import dispose_engine  # noqa: E402
from app.db_tools import apply_init_scripts  # noqa: E402
from app.main import create_app  # noqa: E402
from app.settings import get_settings  # noqa: E402
from tests import testdb  # noqa: E402


@pytest.fixture(scope="session")
def database_url(request: pytest.FixtureRequest) -> str:
    """URL of the dedicated test database, or skip. An unsafe database stops the whole run."""
    try:
        url = testdb.resolve(use_dotenv=request.config.getoption("--test-db"))
    except testdb.UnsafeTestDatabase as exc:
        pytest.exit(str(exc), returncode=2)
    if url is None:
        pytest.skip("no test database: run 'pytest --test-db' or set AQUAMAP_TEST_DATABASE_URL")
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def db_engine(database_url: str) -> Iterator[Engine]:
    try:
        testdb.create_if_missing(testdb.ensure_safe(database_url))
        engine = create_engine(database_url)
        apply_init_scripts(engine)
    except DBAPIError as exc:
        pytest.skip(f"test database not reachable ({type(exc).__name__}): is PostGIS running? (docker compose up -d db)")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    dispose_engine()
