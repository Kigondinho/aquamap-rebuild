"""The separate test database: where it is, the rule that protects other databases, and its creation.

Database tests never run against the development or production database:

* The database name must end in ``_test``. Anything else is refused before a single statement runs,
  whatever the user passed. There is no override.
* ``pytest --test-db`` derives the test database from the project's ``.env``: same server and
  account, database ``<POSTGRES_DB>_test`` (for example ``aquamap_db_test``), and creates it if it does
  not exist. ``AQUAMAP_TEST_DATABASE_URL`` can name another one explicitly; the same rule applies.
* Within the test database each test runs in a transaction that is rolled back. The few tests that
  must commit (the browser tests, a rollback test, the create-user command) remove what they wrote.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SUFFIX = "_test"
ENV_VAR = "AQUAMAP_TEST_DATABASE_URL"


class UnsafeTestDatabase(RuntimeError):
    """The database offered for testing is not a dedicated test database."""


def ensure_safe(url: URL | str) -> URL:
    """Return the URL if its database is a dedicated test database; raise ``UnsafeTestDatabase`` otherwise."""
    parsed = make_url(url)
    name = parsed.database or ""
    if not name.endswith(REQUIRED_SUFFIX) or name == REQUIRED_SUFFIX:
        raise UnsafeTestDatabase(
            f"Refusing to run database tests against '{name}': the database name must end in '{REQUIRED_SUFFIX}'. "
            f"Use 'pytest --test-db' (creates and uses '<POSTGRES_DB>{REQUIRED_SUFFIX}') or point {ENV_VAR} at a "
            "dedicated test database. Tests delete and rewrite data; they must never touch real data.")
    if not parsed.drivername.startswith("postgresql"):
        raise UnsafeTestDatabase(f"Database tests need PostgreSQL/PostGIS, got '{parsed.drivername}'.")
    return parsed


def url_from_dotenv(env_file: Path | None = None) -> URL:
    """The test database URL derived from the project's ``.env`` (never from the test process environment,
    which ``tests/conftest.py`` points at a closed port on purpose)."""
    from dotenv import dotenv_values

    env_file = env_file or ROOT / ".env"
    if not env_file.exists():
        raise UnsafeTestDatabase(f"{env_file} not found: create it from .env.example, or set {ENV_VAR}.")
    values = dotenv_values(env_file)
    password = values.get("POSTGRES_PASSWORD")
    if not password:
        raise UnsafeTestDatabase(f"POSTGRES_PASSWORD is not set in {env_file}.")
    return URL.create(
        "postgresql+psycopg", username=values.get("POSTGRES_USER") or "aquamap_user", password=password,
        host=values.get("POSTGRES_HOST") or "localhost", port=int(values.get("POSTGRES_PORT") or 5433),
        database=(values.get("POSTGRES_DB") or "aquamap_db") + REQUIRED_SUFFIX)


def resolve(use_dotenv: bool) -> URL | None:
    """The test database to use, already checked; ``None`` if none was requested (database tests then skip)."""
    explicit = os.environ.get(ENV_VAR)
    if explicit:
        return ensure_safe(explicit)
    if use_dotenv:
        return ensure_safe(url_from_dotenv())
    return None


def create_if_missing(url: URL) -> bool:
    """Create the test database on its server if it is not there. Returns True if it was created."""
    url = ensure_safe(url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}).scalar()
            if exists:
                return False
            # The name was checked by ensure_safe; quote it as an identifier.
            quoted = conn.dialect.identifier_preparer.quote(url.database)
            conn.execute(text(f"CREATE DATABASE {quoted}"))
            return True
    finally:
        admin.dispose()


# Every table tests write to. Emptied only in a database that passed ensure_safe().
TABLES = ("road_vulnerability", "road_segments", "river_centerlines", "pipeline_runs", "scoring_config", "spatial_layers",
          "auth_otps", "users")


def empty(engine) -> None:  # noqa: ANN001 - a SQLAlchemy Engine
    """Remove all rows from the application tables of the TEST database (checked again here, not trusted)."""
    ensure_safe(engine.url)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
