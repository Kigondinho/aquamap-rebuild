"""Geometry insertion and spatial queries in EPSG:32737, including GeoJSON output in EPSG:4326."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from sqlalchemy import Connection, text

from tests.db.conftest import NAIROBI_LAT, NAIROBI_LON, ORIGIN_SQL

pytestmark = pytest.mark.db


def test_inserted_geometry_is_stored_in_srid_32737(conn: Connection, insert_road: Callable[..., int]) -> None:
    road = insert_road()
    srid = conn.execute(text("SELECT ST_SRID(geom) FROM road_segments WHERE road_id = :r"), {"r": road}).scalar_one()
    assert srid == 32737


def test_lonlat_geometry_converted_on_insert_has_metric_length(conn: Connection) -> None:
    """0.01 degrees of longitude at Nairobi's latitude is about 1,113 m."""
    road = conn.execute(
        text(
            "INSERT INTO road_segments (highway_type, geom) VALUES ('primary', "
            "ST_Transform(ST_SetSRID(ST_MakeLine(ST_MakePoint(36.82, -1.29), ST_MakePoint(36.83, -1.29)), 4326), 32737)) "
            "RETURNING road_id"
        )
    ).scalar_one()
    length = conn.execute(text("SELECT length_m FROM road_segments WHERE road_id = :r"), {"r": road}).scalar_one()
    assert 1100 < length < 1125


def test_generated_length_matches_construction(conn: Connection, insert_road: Callable[..., int]) -> None:
    road = insert_road(length=750.0)
    length = conn.execute(text("SELECT length_m FROM road_segments WHERE road_id = :r"), {"r": road}).scalar_one()
    assert length == pytest.approx(750.0, abs=1e-6)


def test_geojson_output_in_lonlat_round_trips(conn: Connection, insert_road: Callable[..., int]) -> None:
    road = insert_road()
    geojson = conn.execute(
        text("SELECT ST_AsGeoJSON(ST_Transform(geom, 4326), 6) FROM road_segments WHERE road_id = :r"), {"r": road}
    ).scalar_one()
    geometry = json.loads(geojson)
    assert geometry["type"] == "LineString"
    lon, lat = geometry["coordinates"][0]
    assert lon == pytest.approx(NAIROBI_LON, abs=1e-5)
    assert lat == pytest.approx(NAIROBI_LAT, abs=1e-5)


def test_distance_to_river_is_in_metres(
    conn: Connection, insert_road: Callable[..., int], insert_river: Callable[..., int]
) -> None:
    road = insert_road()
    insert_river(dx=100.0)  # parallel, 100 m east
    insert_river(name="Far River", dx=600.0)
    nearest = conn.execute(
        text(
            "SELECT MIN(ST_Distance(r.geom, v.geom)) FROM road_segments r CROSS JOIN river_centerlines v "
            "WHERE r.road_id = :r"
        ),
        {"r": road},
    ).scalar_one()
    assert nearest == pytest.approx(100.0, abs=1e-6)


def test_dwithin_selects_by_metres(
    conn: Connection, insert_road: Callable[..., int], insert_river: Callable[..., int]
) -> None:
    road = insert_road()
    insert_river(dx=100.0)
    sql = (
        "SELECT count(*) FROM road_segments r JOIN river_centerlines v ON ST_DWithin(r.geom, v.geom, :d) "
        "WHERE r.road_id = :r"
    )
    assert conn.execute(text(sql), {"r": road, "d": 150}).scalar_one() == 1
    assert conn.execute(text(sql), {"r": road, "d": 50}).scalar_one() == 0


def test_nearest_neighbour_ordering(
    conn: Connection, insert_road: Callable[..., int], insert_river: Callable[..., int]
) -> None:
    road = insert_road()
    near, _far = insert_river(name="Near", dx=80.0), insert_river(name="Far", dx=900.0)
    nearest = conn.execute(
        text(
            "SELECT v.river_id FROM river_centerlines v, road_segments r WHERE r.road_id = :r "
            "ORDER BY v.geom <-> r.geom LIMIT 1"
        ),
        {"r": road},
    ).scalar_one()
    assert nearest == near


def test_bbox_query_uses_lonlat_envelope_transformed_to_storage_crs(
    conn: Connection, insert_road: Callable[..., int]
) -> None:
    road = insert_road()
    sql = (
        "SELECT road_id FROM road_segments "
        "WHERE geom && ST_Transform(ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, 4326), 32737)"
    )
    inside = conn.execute(text(sql), {"minx": 36.80, "miny": -1.30, "maxx": 36.84, "maxy": -1.27}).scalars().all()
    outside = conn.execute(text(sql), {"minx": 36.60, "miny": -1.45, "maxx": 36.65, "maxy": -1.40}).scalars().all()
    assert road in inside
    assert road not in outside


def test_view_joins_roads_with_results(
    conn: Connection, insert_road: Callable[..., int], insert_config: Callable[..., int]
) -> None:
    scored, unscored = insert_road(name="Scored"), insert_road(name="Unscored", dx=500.0)
    cfg = insert_config()
    conn.execute(
        text(
            "INSERT INTO road_vulnerability (road_id, config_version, vulnerability_index, risk_category, river_dist_m) "
            "VALUES (:r, :c, 0.812, 'Severe', 34)"
        ),
        {"r": scored, "c": cfg},
    )
    rows = {
        r.name: r
        for r in conn.execute(
            text("SELECT name, risk_category, river_dist_m FROM v_road_risk WHERE road_id IN (:a, :b)"),
            {"a": scored, "b": unscored},
        )
    }
    assert rows["Scored"].risk_category == "Severe" and rows["Scored"].river_dist_m == 34
    assert rows["Unscored"].risk_category is None


def test_spatial_queries_use_the_gist_index(conn: Connection) -> None:
    conn.execute(
        text(
            f"INSERT INTO road_segments (highway_type, geom) "
            f"SELECT 'residential', ST_MakeLine(ST_Translate(o, i * 40, 0), ST_Translate(o, i * 40, 30)) "
            f"FROM (SELECT {ORIGIN_SQL} AS o) b CROSS JOIN generate_series(0, 1999) AS i"
        )
    )
    conn.execute(text("ANALYZE road_segments"))
    conn.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(
        row[0]
        for row in conn.execute(
            text(
                "EXPLAIN SELECT road_id FROM road_segments "
                "WHERE geom && ST_Transform(ST_MakeEnvelope(36.80, -1.30, 36.82, -1.28, 4326), 32737)"
            )
        )
    )
    assert "road_segments_geom_gist" in plan
