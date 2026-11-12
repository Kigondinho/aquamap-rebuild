"""Shared test setup.

Environment variables are set *before* the application is imported so tests never depend on a
developer's real ``.env``. The throw-away password is generated per run, and the database host
points at a closed port: tests that need a real database are marked ``db`` and use
``AQUAMAP_TEST_DATABASE_URL`` instead (see tests/db/conftest.py).
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
}
os.environ.update(TEST_ENV)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import dispose_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.settings import get_settings  # noqa: E402


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    dispose_engine()
