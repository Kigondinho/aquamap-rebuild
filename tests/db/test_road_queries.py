"""Road queries: filtering, spatial accuracy and agreement with the stored analysis results.

All roads are SYNTHETIC. The fixture empties the road tables inside the test transaction (rolled back
afterwards), so every expected answer is known exactly. Spatial answers are also checked against an
independent calculation with pyproj and Shapely on the stored coordinates.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from app.errors import InvalidRequestError, NotFoundError
from app.services import admin as admin_service
from app.services import roads_query as q
from core import ivi
from tests.db.conftest import NAIROBI_LAT, NAIROBI_LON

pytestmark = pytest.mark.db

CFG = ivi.ScoringConfig()
# name, highway, surface, dx (m east of the origin), stored index (None = result without index, "none" = no result)
ROADS = [
    ("Alpha Road", "primary", "asphalt", 100.0, 4.6),
    ("Alpha Lane", "residential", None, 499.9, 4.0),      # exactly on the Severe threshold; just inside 500 m
    ("Beta Street", "residential", "dirt", 500.1, 3.999),  # just under the threshold; just outside 500 m
    ("beta street", "residential", None, 900.0, 3.0),      # same name, different capitals
    ("Gamma 100% Way", "service", None, 1500.0, 2.0),
    ("Delta_Road", "tertiary", "asphalt", -300.0, 1.999),
    (None, "footway", None, -50.0, "none"),                # not eligible: no result row at all
    ("Epsilon Road", "residential", None, 2500.0, None),   # analysed, but no terrain data: no index
]


@pytest.fixture
def db(conn: Connection) -> Iterator[Session]:
    with Session(bind=conn, join_transaction_mode="create_savepoint") as session:
        yield session


@pytest.fixture
def world(conn: Connection, insert_road: Callable[..., int], insert_config: Callable[..., int]) -> dict[str | None, int]:
    conn.execute(text("DELETE FROM road_vulnerability"))
    conn.execute(text("DELETE FROM road_segments"))
    conn.execute(text("UPDATE scoring_config SET is_active = FALSE"))
    cfg = insert_config(active=True)
    ids: dict[str | None, int] = {}
    for name, highway, surface, dx, index in ROADS:
        road = insert_road(name=name, highway=highway, surface=surface, dx=dx, length=200.0)
        ids[name] = road
        if index == "none":
            continue
        conn.execute(text(
            "INSERT INTO road_vulnerability (road_id, config_version, vulnerability_index, risk_category, elevation_m, "
            "slope_pct, river_dist_m, score_elevation, score_slope, score_river, score_surface, data_flags, "
            "model_version, terrain_samples) VALUES (:r, :c, :i, :t, :e, 4.5, 120, :se, 4, 4, 3, :f, 'test-model', 8)"),
            {"r": road, "c": cfg, "i": index, "t": ivi.assign_tier(index, CFG) if index is not None else None,
             "e": 1650.0 if index is not None else None, "se": 3 if index is not None else None,
             "f": [] if index is not None else ["no_dem", "no_slope"]})
    ids["__config__"] = cfg
    return ids


def names(collection) -> list[str | None]:
    return [f.properties["name"] for f in collection.features]


WIDE = q.parse_bbox("36.70,-1.40,36.95,-1.20")  # contains every synthetic road


# ---------------------------------------------------------------- retrieving segments
def test_bbox_returns_every_road_ordered_by_index(db: Session, world: dict) -> None:
    result = q.query_roads(db, WIDE)
    assert names(result) == ["Alpha Road", "Alpha Lane", "Beta Street", "beta street", "Gamma 100% Way", "Delta_Road",
                             None, "Epsilon Road"]
    assert result.number_returned == 8 and not result.truncated
    assert result.type == "FeatureCollection" and all(f.geometry["type"] == "LineString" for f in result.features)


def test_bbox_selects_by_location(db: Session, world: dict, conn: Connection) -> None:
    """A box 0.004 degrees (about 445 m) either side of the origin."""
    box = q.parse_bbox(f"{NAIROBI_LON - 0.004},{NAIROBI_LAT - 0.004},{NAIROBI_LON + 0.004},{NAIROBI_LAT + 0.004}")
    assert set(names(q.query_roads(db, box))) == {"Alpha Road", "Delta_Road", None}
    assert set(names(q.query_roads(db, box))) == independent_bbox(conn, box)


def test_limit_keeps_the_highest_scores_and_reports_truncation(db: Session, world: dict) -> None:
    result = q.query_roads(db, WIDE, limit=3)
    assert names(result) == ["Alpha Road", "Alpha Lane", "Beta Street"]
    assert result.truncated and result.number_returned == 3
    assert not q.query_roads(db, WIDE, limit=8).truncated


# ---------------------------------------------------------------- filters
@pytest.mark.parametrize(("tiers", "expected"), [
    (["Severe"], ["Alpha Road", "Alpha Lane"]),
    (["High"], ["Beta Street", "beta street"]),
    (["Medium"], ["Gamma 100% Way"]),
    (["Low"], ["Delta_Road"]),
    (["Low", "Severe"], ["Alpha Road", "Alpha Lane", "Delta_Road"]),
    (["Low", "Medium", "High", "Severe"], ["Alpha Road", "Alpha Lane", "Beta Street", "beta street", "Gamma 100% Way", "Delta_Road"]),
])
def test_filter_by_category(db: Session, world: dict, tiers: list[str], expected: list[str]) -> None:
    assert names(q.query_roads(db, WIDE, tiers=tiers)) == expected


@pytest.mark.parametrize(("lo", "hi", "expected"), [
    (4.0, None, ["Alpha Road", "Alpha Lane"]),                 # minimum is inclusive
    (4.001, None, ["Alpha Road"]),
    (None, 1.999, ["Delta_Road"]),                             # maximum is inclusive
    (None, 1.998, []),
    (3.0, 3.999, ["Beta Street", "beta street"]),
    (3.0, 3.0, ["beta street"]),                               # a single value
    (2.0, 4.0, ["Alpha Lane", "Beta Street", "beta street", "Gamma 100% Way"]),
    (1.0, 5.0, ["Alpha Road", "Alpha Lane", "Beta Street", "beta street", "Gamma 100% Way", "Delta_Road"]),
])
def test_filter_by_minimum_and_maximum_index(db: Session, world: dict, lo, hi, expected: list[str]) -> None:
    assert names(q.query_roads(db, WIDE, min_ivi=lo, max_ivi=hi)) == expected


def test_score_filters_leave_out_roads_without_an_index(db: Session, world: dict) -> None:
    everything = names(q.query_roads(db, WIDE, min_ivi=1.0, max_ivi=5.0))
    assert None not in everything and "Epsilon Road" not in everything


@pytest.mark.parametrize(("classes", "expected"), [
    (["primary"], ["Alpha Road"]),
    (["residential"], ["Alpha Lane", "Beta Street", "beta street", "Epsilon Road"]),
    (["primary", "tertiary"], ["Alpha Road", "Delta_Road"]),
    (["footway"], [None]),
    (["motorway"], []),
    (["PRIMARY"], []),  # OSM classes are lower case; matching is exact
])
def test_filter_by_road_classification(db: Session, world: dict, classes: list[str], expected: list) -> None:
    assert names(q.query_roads(db, WIDE, highways=classes)) == expected


def test_filters_combine_with_and(db: Session, world: dict) -> None:
    assert names(q.query_roads(db, WIDE, tiers=["Severe", "High"], highways=["residential"], min_ivi=3.5)) == \
        ["Alpha Lane", "Beta Street"]
    assert names(q.query_roads(db, WIDE, tiers=["Low"], min_ivi=3.0)) == []


def test_invalid_filters_are_rejected(db: Session, world: dict) -> None:
    with pytest.raises(InvalidRequestError):
        q.query_roads(db, WIDE, min_ivi=4.0, max_ivi=3.0)
    with pytest.raises(InvalidRequestError):
        q.query_roads(db, WIDE, tiers=["Critical"])


# ---------------------------------------------------------------- search by name
def test_search_is_case_insensitive_and_groups_segments_by_name(db: Session, world: dict) -> None:
    result = q.search_roads(db, "BETA")
    assert len(result.features) == 1
    p = result.features[0].properties
    assert p["name"] in ("Beta Street", "beta street") and p["segments"] == 2 and p["segments_scored"] == 2
    assert p["length_m"] == pytest.approx(400.0)
    assert (p["vulnerability_index"], p["risk_category"], p["id"]) == (3.999, "High", world["Beta Street"])
    assert result.features[0].geometry["type"] == "MultiLineString" and len(result.features[0].geometry["coordinates"]) == 2
    west, south, east, north = p["bbox"]
    assert west < east and south < north and NAIROBI_LON < west and abs(south - NAIROBI_LAT) < 1e-5


def test_search_puts_names_starting_with_the_text_first(db: Session, world: dict) -> None:
    assert names(q.search_roads(db, "road")) == ["Alpha Road", "Delta_Road", "Epsilon Road"]  # contains, alphabetical
    assert names(q.search_roads(db, "alpha")) == ["Alpha Lane", "Alpha Road"]
    assert names(q.search_roads(db, "a ro")) == ["Alpha Road"]
    assert names(q.search_roads(db, "  alpha   road ")) == ["Alpha Road"]
    assert names(q.search_roads(db, "lane"))[0] == "Alpha Lane"
    assert names(q.search_roads(db, "de")) == ["Delta_Road"]


def test_search_treats_wildcard_characters_literally(db: Session, world: dict) -> None:
    assert names(q.search_roads(db, "100%")) == ["Gamma 100% Way"]
    assert names(q.search_roads(db, "a_r")) == ["Delta_Road"]       # "_" is not "any character": Alpha Road does not match
    assert names(q.search_roads(db, "%%")) == []
    assert names(q.search_roads(db, "zzz")) == []
    assert names(q.search_roads(db, "'; DROP TABLE road_segments;--")) == []
    assert q.query_roads(db, WIDE).number_returned == 8


def test_search_limit_and_minimum_length(db: Session, world: dict) -> None:
    limited = q.search_roads(db, "road", limit=2)
    assert names(limited) == ["Alpha Road", "Delta_Road"] and limited.truncated
    with pytest.raises(InvalidRequestError):
        q.search_roads(db, " a ")


def test_search_reports_roads_without_an_index(db: Session, world: dict) -> None:
    p = q.search_roads(db, "epsilon").features[0].properties
    assert (p["vulnerability_index"], p["risk_category"], p["segments_scored"]) == (None, None, 0)


# ---------------------------------------------------------------- radius
def test_radius_returns_roads_within_the_distance_nearest_first(db: Session, world: dict) -> None:
    result = q.nearby_roads(db, NAIROBI_LAT, NAIROBI_LON, radius_m=500.0)
    assert names(result) == [None, "Alpha Road", "Delta_Road", "Alpha Lane"]
    distances = [f.properties["distance_m"] for f in result.features]
    assert distances == pytest.approx([50.0, 100.0, 300.0, 499.9], abs=1e-6)  # metres, not degrees


@pytest.mark.parametrize(("radius", "count"), [(49.9, 0), (50.1, 1), (100.1, 2), (499.8, 3), (500.0, 4), (500.2, 5),
                                               (900.1, 6), (2000.0, 7)])
def test_radius_boundaries(db: Session, world: dict, radius: float, count: int) -> None:
    assert q.nearby_roads(db, NAIROBI_LAT, NAIROBI_LON, radius_m=radius, limit=100).number_returned == count


def test_radius_measures_to_the_nearest_part_of_the_line(db: Session, world: dict, insert_road: Callable[..., int]) -> None:
    """A road whose centre is 1,300 m away but whose southern end is 300 m north of the point."""
    insert_road(name="Long Road", dx=0.0, dy=300.0, length=2000.0)
    result = q.nearby_roads(db, NAIROBI_LAT, NAIROBI_LON, radius_m=310.0, limit=100)
    found = {f.properties["name"]: f.properties["distance_m"] for f in result.features}
    assert found["Long Road"] == pytest.approx(300.0, abs=1e-6)


def test_radius_with_filters_and_limit(db: Session, world: dict) -> None:
    assert names(q.nearby_roads(db, NAIROBI_LAT, NAIROBI_LON, 2000.0, tiers=["Severe"])) == ["Alpha Road", "Alpha Lane"]
    assert names(q.nearby_roads(db, NAIROBI_LAT, NAIROBI_LON, 2000.0, highways=["residential"], max_ivi=3.5)) == ["beta street"]
    two = q.nearby_roads(db, NAIROBI_LAT, NAIROBI_LON, 2000.0, limit=2)
    assert names(two) == [None, "Alpha Road"] and two.truncated


@pytest.mark.parametrize("kwargs", [{"radius_m": 0}, {"radius_m": -5}, {"radius_m": 2000.1}, {"radius_m": float("nan")},
                                    {"lat": 91.0}, {"lon": 181.0}])
def test_radius_rejects_bad_input(db: Session, world: dict, kwargs: dict) -> None:
    args = {"lat": NAIROBI_LAT, "lon": NAIROBI_LON, "radius_m": 500.0} | kwargs
    with pytest.raises(InvalidRequestError):
        q.nearby_roads(db, **args)


def test_point_far_from_any_road_gives_an_empty_collection(db: Session, world: dict) -> None:
    result = q.nearby_roads(db, -1.40, 36.65, 500.0)
    assert result.features == [] and result.number_returned == 0 and not result.truncated


# ---------------------------------------------------------------- independent spatial cross-check
def stored_lines(conn: Connection) -> dict[int, object]:
    from shapely import wkb

    return {r[0]: wkb.loads(bytes(r[1])) for r in conn.execute(text("SELECT road_id, ST_AsBinary(geom) FROM road_segments"))}


def to_utm(lon: float, lat: float) -> tuple[float, float]:
    from pyproj import Transformer

    return Transformer.from_crs(4326, 32737, always_xy=True).transform(lon, lat)


def independent_bbox(conn: Connection, box) -> set:
    from shapely.geometry import Polygon

    corners = [(box.min_lon, box.min_lat), (box.min_lon, box.max_lat), (box.max_lon, box.max_lat), (box.max_lon, box.min_lat)]
    polygon = Polygon([to_utm(*c) for c in corners])
    hit = {road for road, line in stored_lines(conn).items() if line.intersects(polygon)}
    return {r[0] for r in conn.execute(text("SELECT name FROM road_segments WHERE road_id = ANY(:ids)"), {"ids": list(hit)})}


@pytest.fixture
def random_world(conn: Connection, world: dict) -> None:
    """150 random two-point lines within about 3 km of the origin (seeded, so repeatable)."""
    rng = random.Random(20261113)
    x0, y0 = to_utm(NAIROBI_LON, NAIROBI_LAT)
    for i in range(150):
        x, y = x0 + rng.uniform(-3000, 3000), y0 + rng.uniform(-3000, 3000)
        conn.execute(text("INSERT INTO road_segments (name, highway_type, geom) VALUES (:n, 'service', "
                          "ST_SetSRID(ST_MakeLine(ST_MakePoint(:x1, :y1), ST_MakePoint(:x2, :y2)), 32737))"),
                     {"n": f"rnd{i}", "x1": x, "y1": y, "x2": x + rng.uniform(-400, 400), "y2": y + rng.uniform(-400, 400)})


@pytest.mark.parametrize(("lat", "lon", "radius"), [(-1.29, 36.82, 500.0), (-1.29, 36.82, 1999.0), (-1.2815, 36.8312, 750.0),
                                                    (-1.3007, 36.8091, 120.0), (-1.305, 36.84, 1500.0)])
def test_radius_matches_an_independent_calculation(db: Session, conn: Connection, random_world: None,
                                                   lat: float, lon: float, radius: float) -> None:
    from shapely.geometry import Point

    point = Point(*to_utm(lon, lat))
    expected = {road: line.distance(point) for road, line in stored_lines(conn).items() if line.distance(point) <= radius}
    result = q.nearby_roads(db, lat, lon, radius, limit=1000)
    got = {f.properties["id"]: f.properties["distance_m"] for f in result.features}
    assert set(got) == set(expected) and len(expected) > 0
    assert all(abs(got[r] - expected[r]) < 1e-3 for r in got)          # agree to a millimetre
    assert list(got.values()) == sorted(got.values())                  # nearest first


@pytest.mark.parametrize("raw", ["36.81,-1.30,36.83,-1.28", "36.80,-1.31,36.815,-1.295", "36.825,-1.285,36.85,-1.27",
                                 "36.8195,-1.2905,36.8205,-1.2895"])
def test_bbox_matches_an_independent_calculation(db: Session, conn: Connection, random_world: None, raw: str) -> None:
    box = q.parse_bbox(raw)
    result = q.query_roads(db, box, limit=10000)
    assert set(names(result)) == independent_bbox(conn, box)


# ---------------------------------------------------------------- returned values = stored values
def test_list_values_are_the_stored_results(db: Session, conn: Connection, world: dict) -> None:
    stored = {r.road_id: r for r in conn.execute(text(
        "SELECT r.road_id, r.name, r.highway_type, v.vulnerability_index, v.risk_category, "
        "ST_AsGeoJSON(ST_Transform(r.geom, 4326), 6) AS geometry FROM road_segments r "
        "LEFT JOIN road_vulnerability v USING (road_id)"))}
    for result in (q.query_roads(db, WIDE), q.nearby_roads(db, NAIROBI_LAT, NAIROBI_LON, 2000.0, limit=100)):
        assert result.number_returned >= 7
        for feature in result.features:
            p, row = feature.properties, stored[feature.properties["id"]]
            assert (p["name"], p["highway_type"], p["risk_category"]) == (row.name, row.highway_type, row.risk_category)
            assert p["vulnerability_index"] == (float(row.vulnerability_index) if row.vulnerability_index is not None else None)
            assert feature.geometry == json.loads(row.geometry)        # exact stored geometry, not simplified


def test_returned_coordinates_are_the_stored_line_in_lon_lat(db: Session, conn: Connection, world: dict) -> None:
    from pyproj import Transformer

    back = Transformer.from_crs(32737, 4326, always_xy=True)
    lines = stored_lines(conn)
    for feature in q.query_roads(db, WIDE).features:
        expected = [back.transform(x, y) for x, y in lines[feature.properties["id"]].coords]
        assert len(feature.geometry["coordinates"]) == len(expected)
        for (lon, lat), (elon, elat) in zip(feature.geometry["coordinates"], expected, strict=True):
            assert abs(lon - elon) < 1e-6 and abs(lat - elat) < 1e-6    # within GeoJSON rounding (about 0.1 m)


def test_every_returned_category_matches_its_score(db: Session, world: dict) -> None:
    for feature in q.query_roads(db, WIDE).features:
        p = feature.properties
        if p["vulnerability_index"] is None:
            assert p["risk_category"] is None
        else:
            assert p["risk_category"] == ivi.assign_tier(p["vulnerability_index"], CFG)


def test_zoom_simplifies_only_on_request(db: Session, conn: Connection, world: dict) -> None:
    conn.execute(text("INSERT INTO road_segments (name, highway_type, geom) SELECT 'Wiggly', 'service', "
                      "ST_SetSRID(ST_MakeLine(array_agg(ST_MakePoint(ST_X(o) + i * 10, ST_Y(o) - 800 + (i % 2) * 2) ORDER BY i)), 32737) "
                      "FROM generate_series(0, 40) i, (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(36.82, -1.29), 4326), 32737) o) s"))
    count = lambda zoom: next(len(f.geometry["coordinates"]) for f in q.query_roads(db, WIDE, zoom=zoom).features  # noqa: E731
                              if f.properties["name"] == "Wiggly")
    assert count(None) == 41 and count(16) == 41 and count(12) == 2


# ---------------------------------------------------------------- one road
def test_detail_returns_every_stored_value(db: Session, conn: Connection, world: dict) -> None:
    road = world["Alpha Road"]
    feature = q.get_road(db, road)
    p = feature.properties
    row = conn.execute(text("SELECT * FROM road_vulnerability WHERE road_id = :r"), {"r": road}).mappings().one()
    assert (p["id"], p["name"], p["highway_type"], p["surface"]) == (road, "Alpha Road", "primary", "asphalt")
    assert p["scored"] is True and p["vulnerability_index"] == float(row["vulnerability_index"]) == 4.6
    assert p["risk_category"] == row["risk_category"] == "Severe"
    assert (p["elevation_m"], p["slope_pct"], p["river_dist_m"]) == (row["elevation_m"], row["slope_pct"], row["river_dist_m"])
    assert p["factor_scores"] == {"elevation": 3.0, "slope": 4.0, "river": 4.0, "surface": 3.0}
    assert p["weights"] == {"elevation": 0.35, "slope": 0.2, "river": 0.3, "surface": 0.15}
    assert p["tier_thresholds"] == {"Medium": 2.0, "High": 3.0, "Severe": 4.0}
    assert (p["config_version"], p["model_version"], p["terrain_samples"]) == (world["__config__"], "test-model", 8)
    assert p["computed_at"] is not None and p["data_flags"] == []
    assert p["length_m"] == pytest.approx(200.0)
    assert feature.geometry["type"] == "LineString" and feature.geometry["coordinates"][0][0] == pytest.approx(36.8209, abs=1e-4)


def test_detail_of_a_road_without_a_result(db: Session, world: dict) -> None:
    p = q.get_road(db, world[None]).properties
    assert p["scored"] is False and p["vulnerability_index"] is None and p["risk_category"] is None
    assert p["factor_scores"] is None and p["weights"] is None and p["config_version"] is None and p["data_flags"] == []


def test_detail_of_a_road_analysed_without_terrain_data(db: Session, world: dict) -> None:
    p = q.get_road(db, world["Epsilon Road"]).properties
    assert p["scored"] is False and p["vulnerability_index"] is None and p["risk_category"] is None
    assert p["data_flags"] == ["no_dem", "no_slope"] and p["elevation_m"] is None
    assert p["factor_scores"] == {"elevation": None, "slope": 4.0, "river": 4.0, "surface": 3.0}


def test_detail_of_a_missing_road_is_not_found(db: Session, world: dict) -> None:
    with pytest.raises(NotFoundError):
        q.get_road(db, 2_000_000_000)


# ---------------------------------------------------------------- totals and classification metadata
def test_summary_counts_and_lengths(db: Session, world: dict) -> None:
    stats = q.road_stats(db)
    assert stats.total_segments == 8 and stats.total_length_km == pytest.approx(1.6)
    assert {t.tier: t.segments for t in stats.by_tier} == {"Low": 1, "Medium": 1, "High": 2, "Severe": 2}
    assert stats.unscored_segments == 2 and stats.unscored_length_km == pytest.approx(0.4)
    severe = q.road_stats(db, tiers=["Severe"], highways=["primary"])
    assert severe.total_segments == 1 and severe.unscored_segments == 0
    near = q.road_stats(db, bbox=q.parse_bbox(f"{NAIROBI_LON - 0.004},{NAIROBI_LAT - 0.004},{NAIROBI_LON + 0.004},{NAIROBI_LAT + 0.004}"))
    assert near.total_segments == 3


def test_meta_reports_the_thresholds_of_the_active_configuration(db: Session, world: dict) -> None:
    meta = admin_service.get_public_meta(db)
    assert [(t.tier, t.lower_bound, t.upper_bound) for t in meta.tiers] == \
        [("Low", 1.0, 2.0), ("Medium", 2.0, 3.0), ("High", 3.0, 4.0), ("Severe", 4.0, None)]
    assert meta.config_version == world["__config__"] and sum(meta.weights.values()) == pytest.approx(1.0)
    assert "higher" in meta.boundary_rule and "not a flood" in meta.disclaimer.lower()
    assert (meta.scale_min, meta.scale_max) == (1.0, 5.0)


# ---------------------------------------------------------------- through HTTP
@pytest.fixture
def http(db: Session, world: dict):
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.deps import get_current_user
    from app.main import create_app
    from app.schemas.auth import CurrentUser

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=uuid4(), email="tester@example.com", full_name="Tester", role="user")
    with TestClient(app) as client:
        yield client


def test_http_list_with_all_filters(http, world: dict) -> None:
    response = http.get("/api/v1/roads", params={"bbox": "36.70,-1.40,36.95,-1.20", "tier": ["Severe", "High"],
                                                 "highway": ["residential"], "min_ivi": 3.5, "max_ivi": 4.0})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [f["properties"]["name"] for f in body["features"]] == ["Alpha Lane", "Beta Street"]
    assert body["type"] == "FeatureCollection" and body["truncated"] is False
    assert set(body["features"][0]["properties"]) == {"id", "name", "highway_type", "risk_category", "vulnerability_index"}


def test_http_search_nearby_detail_summary_meta(http, world: dict) -> None:
    assert [f["properties"]["name"] for f in http.get("/api/v1/roads/search", params={"q": "alpha"}).json()["features"]] == \
        ["Alpha Lane", "Alpha Road"]
    near = http.get("/api/v1/roads/nearby", params={"lat": NAIROBI_LAT, "lon": NAIROBI_LON, "radius_m": 150}).json()
    assert [round(f["properties"]["distance_m"], 3) for f in near["features"]] == [50.0, 100.0]
    detail = http.get(f"/api/v1/roads/{world['Alpha Road']}").json()
    assert detail["type"] == "Feature" and detail["properties"]["vulnerability_index"] == 4.6
    assert http.get("/api/v1/roads/summary").json()["total_segments"] == 8
    assert [t["tier"] for t in http.get("/api/v1/meta/config").json()["tiers"]] == ["Low", "Medium", "High", "Severe"]


@pytest.mark.parametrize(("path", "params", "status"), [
    ("/api/v1/roads", {}, 422),                                                        # bbox is required
    ("/api/v1/roads", {"bbox": "1,2,3"}, 422),
    ("/api/v1/roads", {"bbox": "36.7,-1.4,36.9,-1.2", "tier": "Critical"}, 422),
    ("/api/v1/roads", {"bbox": "36.7,-1.4,36.9,-1.2", "min_ivi": 0.5}, 422),
    ("/api/v1/roads", {"bbox": "36.7,-1.4,36.9,-1.2", "max_ivi": 5.5}, 422),
    ("/api/v1/roads", {"bbox": "36.7,-1.4,36.9,-1.2", "min_ivi": 4, "max_ivi": 3}, 422),
    ("/api/v1/roads", {"bbox": "36.7,-1.4,36.9,-1.2", "limit": 0}, 422),
    ("/api/v1/roads/search", {"q": "a"}, 422),
    ("/api/v1/roads/nearby", {"lat": -1.29, "lon": 36.82, "radius_m": 5000}, 422),
    ("/api/v1/roads/nearby", {"lat": -1.29}, 422),
    ("/api/v1/roads/0", {}, 422),
    ("/api/v1/roads/abc", {}, 422),
    ("/api/v1/roads/2000000000", {}, 404),
])
def test_http_rejects_bad_requests(http, path: str, params: dict, status: int) -> None:
    response = http.get(path, params=params)
    assert response.status_code == status, response.text
    assert "detail" in response.json()
