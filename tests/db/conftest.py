"""Fixtures for tests that need a real PostGIS database.

Set ``AQUAMAP_TEST_DATABASE_URL`` to a disposable database, for example the Docker one:

    export AQUAMAP_TEST_DATABASE_URL="postgresql+psycopg://aquamap_user:<password from .env>@localhost:5433/aquamap_db"
    pytest -m db

Without it every ``db`` test is skipped. The init scripts are applied first (they are
idempotent). Each test then runs inside a transaction that is rolled back, so nothing is left behind.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from app.db_tools import apply_init_scripts
from core import config_defaults as defaults

# A point in central Nairobi (lon, lat in EPSG:4326).
NAIROBI_LON, NAIROBI_LAT = 36.82, -1.29

# SQL expression for a Nairobi origin point in the storage CRS (EPSG:32737).
ORIGIN_SQL = "ST_Transform(ST_SetSRID(ST_MakePoint(36.82, -1.29), 4326), 32737)"


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("AQUAMAP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AQUAMAP_TEST_DATABASE_URL not set")
    return url


@pytest.fixture(scope="session")
def db_engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url)
    apply_init_scripts(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def conn(db_engine: Engine) -> Iterator[Connection]:
    """A connection inside a transaction that is always rolled back."""
    with db_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def expect_db_error(connection: Connection, sql: str, params: dict | None = None) -> None:
    """Assert the statement is rejected by the database, keeping the outer transaction usable."""
    with pytest.raises(DBAPIError):
        with connection.begin_nested():
            connection.execute(text(sql), params or {})


@pytest.fixture
def insert_config(conn: Connection) -> Callable[..., int]:
    def _insert(active: bool = False) -> int:
        return conn.execute(
            text(
                "INSERT INTO scoring_config (weights, breakpoints, surface_map, tier_thresholds, is_active) "
                "VALUES (CAST(:w AS jsonb), CAST(:b AS jsonb), CAST(:s AS jsonb), CAST(:t AS jsonb), :a) "
                "RETURNING config_version"
            ),
            {
                "w": json.dumps(defaults.DEFAULT_WEIGHTS),
                "b": json.dumps(defaults.DEFAULT_BREAKPOINTS),
                "s": json.dumps({"asphalt": defaults.SURFACE_SCORE_PAVED}),
                "t": json.dumps(defaults.DEFAULT_TIER_THRESHOLDS),
                "a": active,
            },
        ).scalar_one()

    return _insert


@pytest.fixture
def insert_road(conn: Connection) -> Callable[..., int]:
    """Insert a straight road near central Nairobi, offset in metres from the origin point."""

    def _insert(
        name: str | None = "Test Road",
        highway: str = "residential",
        surface: str | None = "asphalt",
        dx: float = 0.0,
        dy: float = 0.0,
        length: float = 1000.0,
    ) -> int:
        return conn.execute(
            text(
                f"INSERT INTO road_segments (name, highway_type, surface, geom) VALUES "
                f"(:name, :hw, :surface, ST_MakeLine(ST_Translate({ORIGIN_SQL}, :dx, :dy), "
                f"ST_Translate({ORIGIN_SQL}, :dx, :dy2))) RETURNING road_id"
            ),
            {"name": name, "hw": highway, "surface": surface, "dx": dx, "dy": dy, "dy2": dy + length},
        ).scalar_one()

    return _insert


@pytest.fixture
def insert_river(conn: Connection) -> Callable[..., int]:
    def _insert(name: str | None = "Test River", dx: float = 100.0, dy: float = 0.0, length: float = 1000.0) -> int:
        return conn.execute(
            text(
                f"INSERT INTO river_centerlines (name, waterway, geom) VALUES "
                f"(:name, 'river', ST_MakeLine(ST_Translate({ORIGIN_SQL}, :dx, :dy), "
                f"ST_Translate({ORIGIN_SQL}, :dx, :dy2))) RETURNING river_id"
            ),
            {"name": name, "dx": dx, "dy": dy, "dy2": dy + length},
        ).scalar_one()

    return _insert
