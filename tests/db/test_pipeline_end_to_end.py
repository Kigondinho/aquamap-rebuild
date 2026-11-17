"""The whole data pipeline, run with its production entry points on synthetic inputs with known answers.

    roads.gpkg + rivers.gpkg --load_vectors.run--> PostGIS
    raw DEM               --prepare_dem.run---> elevation + slope rasters
    PostGIS + rasters     --score_roads.run---> road_vulnerability
    road_vulnerability    --roads_query------> what the API returns

Inputs are SYNTHETIC: a DEM that rises 1.5 m per 100 m eastwards from 1,550 m at the origin (every road
stays in elevation class 4 and slope class 5), one straight north-south river, and roads placed at chosen
distances from it, so every index can be worked out by hand. (A perfectly flat DEM is refused by the
terrain validator on purpose: all slopes below 1 % usually means slope was saved in the wrong unit.) The pipeline
commits, so this module empties the TEST database before and after (``tests.testdb`` guarantees that
can only be a database whose name ends in ``_test``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.db

gpd = pytest.importorskip("geopandas")
np = pytest.importorskip("numpy")
rasterio = pytest.importorskip("rasterio")

from pyproj import Transformer  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from shapely.geometry import LineString, MultiLineString  # noqa: E402

from app.services import roads_query  # noqa: E402
from core.config_defaults import MODEL_VERSION  # noqa: E402
from pipeline import load_vectors, prepare_dem, score_roads  # noqa: E402
from tests import testdb  # noqa: E402

TO_UTM = Transformer.from_crs(4326, 32737, always_xy=True)
TO_LONLAT = Transformer.from_crs(32737, 4326, always_xy=True)
OX, OY = TO_UTM.transform(36.82, -1.29)   # origin in metres, EPSG:32737

# name, OSM class, surface, metres east of the river, expected river distance, expected IVI, category, flags
# IVI = 0.35*E + 0.20*S + 0.30*D + 0.15*M with E = 4 (1,541-1,559 m) and S = 5 (1.5 %) for every road:
ROADS = [
    ("Near Road", "primary", "dirt", 40.0, 40.0, 4.65, "Severe", []),               # D 5, M 5
    ("Middle Road", "tertiary", None, -200.0, 200.0, 3.75, "High", ["surface_missing"]),  # D 3, M 3
    ("Far Road", "residential", "asphalt", 600.0, 600.0, 3.00, "High", []),         # D 1, M 2: exactly on the High bound
]
SPLIT = ("Split Road", "service", "asphalt", -400.0, 400.0, 3.30, "High", [])        # D 2, M 2; a MultiLineString, 2 parts
FOOTWAY_X = 300.0                                                                   # not an eligible class: never scored
SLOPE = 0.015                                  # rise per metre eastwards
METRES_PER_DEGREE_LON = 111_320 * np.cos(np.radians(1.29))


def expected_elevation(x_offset: float) -> float:
    return 1550.0 + SLOPE * x_offset


def lonlat_line(x_offset: float, y_from: float = -200.0, y_to: float = 200.0) -> LineString:
    return LineString([TO_LONLAT.transform(OX + x_offset, OY + y) for y in (y_from, y_to)])


def write_inputs(data_dir: Path) -> Path:
    """Raw DEM (EPSG:4326, like the downloaded SRTM) and the processed vector files the loader reads."""
    processed = data_dir / "processed"
    processed.mkdir(parents=True)
    rows = [{"element": "way", "osm_id": 1000 + i, "name": n, "highway": h, "surface": s, "geometry": lonlat_line(x)}
            for i, (n, h, s, x, *_rest) in enumerate(ROADS)]
    rows.append({"element": "way", "osm_id": 2000, "name": SPLIT[0], "highway": SPLIT[1], "surface": SPLIT[2],
                 "geometry": MultiLineString([lonlat_line(SPLIT[3], -300, -100), lonlat_line(SPLIT[3], 100, 300)])})
    rows.append({"element": "way", "osm_id": 3000, "name": None, "highway": "footway", "surface": None,
                 "geometry": lonlat_line(FOOTWAY_X)})
    gpd.GeoDataFrame(rows, geometry="geometry", crs=4326).to_file(processed / "roads.gpkg", driver="GPKG")
    river = [{"element": "way", "osm_id": 5000, "name": "Test River", "waterway": "river",
              "geometry": lonlat_line(0.0, -800.0, 800.0)}]
    gpd.GeoDataFrame(river, geometry="geometry", crs=4326).to_file(processed / "rivers.gpkg", driver="GPKG")

    cell = 1 / 3600   # one arc-second, as SRTM
    size = int(0.04 / cell)
    raw = data_dir / "raw" / "dem" / "plane.tif"
    raw.parent.mkdir(parents=True)
    lon = 36.80 + (np.arange(size) + 0.5) * cell                       # cell centres
    plane = 1550.0 + SLOPE * (lon - 36.82) * METRES_PER_DEGREE_LON       # float: a plane stays a plane when reprojected
    with rasterio.open(raw, "w", driver="GTiff", height=size, width=size, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(36.80, -1.27, cell, cell), nodata=-32768) as dst:
        dst.write(np.tile(plane, (size, 1)).astype("float32"), 1)
    return raw


@pytest.fixture(scope="module")
def pipeline_run(db_engine: Engine, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict]:
    testdb.empty(db_engine)
    data_dir = tmp_path_factory.mktemp("pipeline")
    raw_dem = write_inputs(data_dir)
    try:
        yield {
            "data_dir": data_dir,
            "load": load_vectors.run(data_dir, db_engine),
            "terrain": prepare_dem.run(data_dir, raw_dem=raw_dem, force=True),
            "score": score_roads.run(data_dir, db_engine),
        }
    finally:
        testdb.empty(db_engine)


@pytest.fixture
def db(db_engine: Engine, pipeline_run: dict) -> Iterator[Session]:
    with Session(db_engine) as session:
        yield session


def road_id(session: Session, name: str, part: int = 0) -> int:
    return session.execute(text("SELECT road_id FROM road_segments WHERE name = :n AND part_no = :p"),
                           {"n": name, "p": part}).scalar_one()


# ---------------------------------------------------------------- each stage
def test_ingestion_loaded_and_verified_every_feature(pipeline_run: dict) -> None:
    report = pipeline_run["load"]
    assert report["all_ok"], report
    roads, rivers = report["layers"]["roads"], report["layers"]["rivers"]
    assert roads["load"]["inserted"] == 6 and roads["load"]["rows_in_database"] == 6   # 3 roads + 2 parts + footway
    assert roads["load"]["cleaning"]["multilines_split_extra_parts"] == 1
    assert rivers["load"]["inserted"] == 1
    assert roads["verify"]["stats"]["srids"] == [32737] and roads["verify"]["stats"]["geometry_mismatches"] == 0


def test_terrain_rasters_are_valid_with_the_known_slope(pipeline_run: dict) -> None:
    result = pipeline_run["terrain"]
    assert result["ok"], result["errors"]
    stats = result["stats"]
    assert stats["crs"] == "EPSG:32737" and stats["cell"] == [30.0, 30.0]
    assert 1500 < stats["elevation_min"] < 1550 < stats["elevation_max"] < 1600
    assert stats["slope_median"] == pytest.approx(SLOPE * 100, abs=0.02)    # 1.5 %, in percent
    assert stats["slope_max"] == pytest.approx(SLOPE * 100, abs=0.05)


def test_scoring_scored_every_eligible_road_and_recorded_the_run(pipeline_run: dict, db: Session) -> None:
    report = pipeline_run["score"]
    assert report["ok"] and report["model_version"] == MODEL_VERSION
    assert (report["roads_in_database"], report["roads_eligible"], report["roads_not_eligible"]) == (6, 5, 1)
    summary = report["summary"]
    assert summary["segments_scored"] == 5 and summary["segments_without_index"] == 0
    assert {t: v["segments"] for t, v in summary["by_tier"].items()} == {"Low": 0, "Medium": 0, "High": 4, "Severe": 1}
    run = db.execute(text("SELECT status, model_version, segments_scored FROM pipeline_runs WHERE run_id = :r"),
                     {"r": report["run_id"]}).one()
    assert tuple(run) == ("succeeded", MODEL_VERSION, 5)
    assert report["verification"]["formula_mismatch"] == 0 and report["verification"]["tier_mismatch"] == 0


# ---------------------------------------------------------------- what the API returns at the end
@pytest.mark.parametrize("road", [*ROADS, SPLIT], ids=lambda r: r[0])
def test_each_road_has_the_hand_calculated_result(db: Session, road: tuple) -> None:
    name, highway, surface, x_offset, distance, ivi, category, flags = road
    detail = roads_query.get_road(db, road_id(db, name)).properties
    assert (detail["name"], detail["highway_type"], detail["surface"]) == (name, highway, surface)
    assert detail["vulnerability_index"] == ivi and detail["risk_category"] == category
    assert detail["elevation_m"] == pytest.approx(expected_elevation(x_offset), abs=0.5)
    assert detail["slope_pct"] == pytest.approx(SLOPE * 100, abs=0.05)
    assert detail["river_dist_m"] == pytest.approx(distance, abs=0.05)
    assert detail["factor_scores"]["elevation"] == 4 and detail["factor_scores"]["slope"] == 5
    assert detail["data_flags"] == flags and detail["model_version"] == MODEL_VERSION
    assert detail["terrain_samples"] >= 3


def test_both_parts_of_a_split_feature_are_scored(db: Session) -> None:
    parts = [roads_query.get_road(db, road_id(db, SPLIT[0], part)).properties for part in (0, 1)]
    assert [p["vulnerability_index"] for p in parts] == [SPLIT[5], SPLIT[5]]
    assert parts[0]["osm_id"] == parts[1]["osm_id"] == 2000 and parts[0]["id"] != parts[1]["id"]


def test_the_footway_is_stored_but_not_scored(db: Session) -> None:
    footway = db.execute(text("SELECT road_id FROM road_segments WHERE highway_type = 'footway'")).scalar_one()
    detail = roads_query.get_road(db, footway).properties
    assert detail["scored"] is False and detail["vulnerability_index"] is None and detail["config_version"] is None


def test_queries_over_the_pipeline_output(db: Session) -> None:
    box = roads_query.parse_bbox("36.80,-1.31,36.84,-1.27")
    everything = roads_query.query_roads(db, box)
    assert everything.pagination.total == 6
    assert [f.properties["name"] for f in everything.features][:1] == ["Near Road"]          # highest index first
    severe = roads_query.query_roads(db, box, tiers=["Severe"])
    assert [f.properties["name"] for f in severe.features] == ["Near Road"]
    on_bound = roads_query.query_roads(db, box, min_ivi=3.0, max_ivi=3.0)
    assert [f.properties["name"] for f in on_bound.features] == ["Far Road"]                  # 3.000 is High, and inclusive
    lon, lat = TO_LONLAT.transform(OX + 40.0, OY)                                             # on Near Road
    near = roads_query.nearby_roads(db, lat, lon, radius_m=100.0)
    assert [f.properties["name"] for f in near.features] == ["Near Road"]
    assert near.features[0].properties["distance_m"] == pytest.approx(0.0, abs=0.05)
    found = roads_query.search_roads(db, "split")
    assert found.features[0].properties["segments"] == 2
    stats = roads_query.road_stats(db)
    assert stats.total_segments == 6 and stats.unscored_segments == 1


# ---------------------------------------------------------------- running again
def test_running_the_pipeline_again_changes_nothing(pipeline_run: dict, db_engine: Engine, db: Session) -> None:
    before = db.execute(text("SELECT road_id, vulnerability_index, risk_category FROM road_vulnerability ORDER BY road_id")).all()
    again = load_vectors.run(pipeline_run["data_dir"], db_engine)
    assert (again["layers"]["roads"]["load"]["inserted"], again["layers"]["roads"]["load"]["updated"]) == (0, 0)
    rescored = score_roads.run(pipeline_run["data_dir"], db_engine)
    assert rescored["results_written"] == 5 and rescored["results_removed_not_eligible"] == 0
    assert rescored["run_id"] != pipeline_run["score"]["run_id"]
    db.expire_all()
    after = db.execute(text("SELECT road_id, vulnerability_index, risk_category FROM road_vulnerability ORDER BY road_id")).all()
    assert after == before                                                                    # same roads, same scores
    assert db.execute(text("SELECT count(*) FROM road_segments")).scalar_one() == 6
