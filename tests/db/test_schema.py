"""Static checks on the init SQL. These run without a database."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.db_tools import EXPECTED_TABLES, INIT_DIR, init_script_paths


def _all_sql() -> str:
    return "\n".join(p.read_text() for p in init_script_paths())


def test_scripts_exist_in_execution_order() -> None:
    assert [p.name for p in init_script_paths()] == [
        "001_extensions.sql", "002_schema.sql", "003_indexes.sql", "004_views.sql",
        "005_classification.sql",
    ]


def test_postgis_extension_created() -> None:
    assert "CREATE EXTENSION IF NOT EXISTS postgis" in (INIT_DIR / "001_extensions.sql").read_text()


@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_every_table_is_declared(table: str) -> None:
    assert f"CREATE TABLE IF NOT EXISTS {table} " in _all_sql()


def test_geometry_columns_use_projected_srid_32737_only() -> None:
    sql = _all_sql()
    assert sql.count("geometry(LineString, 32737)") == 2
    assert "geometry(LineString, 4326)" not in sql
    assert "32637" not in sql


def test_gist_indexes_on_both_spatial_columns() -> None:
    sql = (INIT_DIR / "003_indexes.sql").read_text()
    assert "ON road_segments     USING GIST (geom)" in sql
    assert "ON river_centerlines USING GIST (geom)" in sql


def test_scripts_are_idempotent() -> None:
    sql = _all_sql()
    assert not re.search(r"CREATE TABLE (?!IF NOT EXISTS)", sql)
    assert not re.search(r"CREATE (UNIQUE )?INDEX (?!IF NOT EXISTS)", sql)
    assert not re.search(r"CREATE VIEW", sql)  # views use CREATE OR REPLACE


def test_scripts_contain_no_credentials() -> None:
    sql = _all_sql()
    assert not re.search(r"(CREATE|ALTER)\s+(ROLE|USER)\b", sql, re.IGNORECASE)
    assert not re.search(r"PASSWORD\s+'", sql, re.IGNORECASE)
