"""The created database objects: tables, SRIDs, spatial and conventional indexes, generated columns."""

from __future__ import annotations

import pytest
from sqlalchemy import Connection, text

from app.db_tools import EXPECTED_TABLES

pytestmark = pytest.mark.db


def test_all_tables_and_the_view_exist(conn: Connection) -> None:
    tables = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"))}
    views = {r[0] for r in conn.execute(text("SELECT viewname FROM pg_views WHERE schemaname = current_schema()"))}
    assert set(EXPECTED_TABLES) <= tables
    assert "v_road_risk" in views


@pytest.mark.parametrize("table", ["road_segments", "river_centerlines"])
def test_geometry_is_linestring_in_epsg_32737(conn: Connection, table: str) -> None:
    row = conn.execute(
        text("SELECT type, srid FROM geometry_columns WHERE f_table_name = :t AND f_geometry_column = 'geom'"),
        {"t": table},
    ).one()
    assert (row.type, row.srid) == ("LINESTRING", 32737)


@pytest.mark.parametrize(
    ("table", "index"),
    [("road_segments", "road_segments_geom_gist"), ("river_centerlines", "river_centerlines_geom_gist")],
)
def test_gist_index_on_geometry_column(conn: Connection, table: str, index: str) -> None:
    definition = conn.execute(
        text("SELECT indexdef FROM pg_indexes WHERE tablename = :t AND indexname = :i"), {"t": table, "i": index}
    ).scalar_one()
    assert "USING gist (geom)" in definition


@pytest.mark.parametrize(
    "index",
    [
        "road_segments_class_idx", "road_segments_name_idx", "road_segments_osm_idx", "road_segments_layer_idx",
        "river_centerlines_layer_idx", "river_centerlines_waterway_idx",
        "road_vulnerability_tier_idx", "road_vulnerability_ivi_idx",
        "road_vulnerability_config_idx", "road_vulnerability_run_idx",
        "auth_otps_user_idx", "pipeline_runs_start_idx", "scoring_config_one_active", "users_email_key",
    ],
)
def test_conventional_index_exists(conn: Connection, index: str) -> None:
    found = conn.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = :i"), {"i": index}).scalar()
    assert found == 1


def test_length_columns_are_generated(conn: Connection) -> None:
    rows = conn.execute(
        text(
            "SELECT c.relname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE a.attname = 'length_m' AND a.attgenerated = 's' AND c.relkind = 'r'"
        )
    ).all()
    assert {r[0] for r in rows} == {"road_segments", "river_centerlines"}
