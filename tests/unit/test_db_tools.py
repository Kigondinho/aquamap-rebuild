"""Diagnostics logic and CLI behaviour that do not need a database."""

from __future__ import annotations

from app import db_tools
from app.db_tools import Diagnostics, EXPECTED_GEOMETRY, EXPECTED_GIST_INDEXES, EXPECTED_TABLES, EXPECTED_VIEWS


def _healthy() -> Diagnostics:
    return Diagnostics(
        server_version="16.4",
        postgis_version="3.4.2",
        tables_present=list(EXPECTED_TABLES),
        views_present=list(EXPECTED_VIEWS),
        geometry_columns=dict(EXPECTED_GEOMETRY),
        gist_indexes=list(EXPECTED_GIST_INDEXES),
    )


def test_expected_geometry_is_epsg_32737() -> None:
    assert {srid for _type, srid in EXPECTED_GEOMETRY.values()} == {32737}


def test_healthy_diagnostics_have_no_problems() -> None:
    assert _healthy().healthy


def test_missing_postgis_and_table_are_reported() -> None:
    d = _healthy()
    d.postgis_version = ""
    d.tables_present.remove("road_segments")
    assert "PostGIS extension is not installed" in d.problems
    assert "missing table: road_segments" in d.problems


def test_wrong_srid_is_reported() -> None:
    d = _healthy()
    d.geometry_columns[("road_segments", "geom")] = ("LINESTRING", 4326)
    assert any("road_segments.geom is LINESTRING/4326" in p for p in d.problems)


def test_missing_gist_index_is_reported() -> None:
    d = _healthy()
    d.gist_indexes.remove("road_segments_geom_gist")
    assert "missing GiST index: road_segments_geom_gist" in d.problems


def test_cli_reports_connection_failure_without_leaking_details(capsys) -> None:
    code = db_tools.main(["check"])
    err = capsys.readouterr().err
    assert code == 1
    assert "Database error" in err
    assert "127.0.0.1" not in err
