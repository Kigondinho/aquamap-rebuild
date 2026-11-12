"""Ingestion into PostGIS: idempotent upsert, validity, SRID, transactions, index, verification.

Rows are SYNTHETIC, use negative OSM ids and their own layer name, so they never collide with real
data. Every test but one runs in a transaction that is rolled back; the exception proves that a
failed ``load_file`` leaves nothing behind in committed state.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import Connection, Engine, text

gpd = pytest.importorskip("geopandas")

from shapely.geometry import LineString, MultiLineString  # noqa: E402

from pipeline import load_vectors as lv  # noqa: E402

pytestmark = pytest.mark.db

LAYER = "pytest_layer"
X, Y = 257000.0, 9857000.0


def roads(n: int = 6, *, shift: float = 0.0, surface: str | None = "asphalt"):
    rows = [{"element": "way", "osm_id": -(i + 1), "name": f"Road {i}", "highway": "residential", "surface": surface,
             "geometry": LineString([(X + i * 50 + shift, Y), (X + i * 50 + shift, Y + 200)])} for i in range(n)]
    rows[0]["geometry"] = MultiLineString([rows[0]["geometry"], LineString([(X - 100, Y), (X - 100, Y + 80)])])
    return lv.prepare(gpd.GeoDataFrame(rows, geometry="geometry", crs=32737), lv.SPECS["roads"])[0]


def rivers(n: int = 3):
    rows = [{"element": "way", "osm_id": -(i + 1), "name": None, "waterway": "stream",
             "geometry": LineString([(X + i * 300, Y - 500), (X + i * 300 + 400, Y - 100)])} for i in range(n)]
    return lv.prepare(gpd.GeoDataFrame(rows, geometry="geometry", crs=32737), lv.SPECS["rivers"])[0]


def count(conn: Connection, table: str = "road_segments") -> int:
    return conn.execute(text(f"SELECT count(*) FROM {table} t JOIN spatial_layers l USING (layer_id) "
                             "WHERE l.layer_name = :n"), {"n": LAYER}).scalar_one()


def test_first_load_inserts_valid_linestrings_in_32737(conn: Connection) -> None:
    frame = roads()
    result = lv.load_layer(conn, frame, lv.SPECS["roads"], layer_name=LAYER)
    assert (result.inserted, result.updated, result.unchanged) == (7, 0, 0)  # 6 ways, one split in two
    assert result.rows_in_database == count(conn) == 7
    row = conn.execute(text(
        "SELECT bool_and(ST_IsValid(geom)), bool_and(NOT ST_IsEmpty(geom)), array_agg(DISTINCT ST_SRID(geom)), "
        "array_agg(DISTINCT GeometryType(geom)), sum(length_m) FROM road_segments WHERE layer_id = :l"),
        {"l": result.layer_id}).one()
    assert row[0] and row[1] and row[2] == [32737] and row[3] == ["LINESTRING"]
    assert row[4] == pytest.approx(float(frame.geometry.length.sum()))
    layer = conn.execute(text("SELECT layer_type, feature_count FROM spatial_layers WHERE layer_id = :l"),
                         {"l": result.layer_id}).one()
    assert tuple(layer) == ("roads", 7)


def test_rerun_changes_nothing_and_creates_no_duplicates(conn: Connection) -> None:
    first = lv.load_layer(conn, roads(), lv.SPECS["roads"], layer_name=LAYER)
    ids = conn.execute(text("SELECT array_agg(road_id ORDER BY road_id) FROM road_segments WHERE layer_id = :l"),
                       {"l": first.layer_id}).scalar_one()
    for _ in range(2):
        again = lv.load_layer(conn, roads(), lv.SPECS["roads"], layer_name=LAYER)
        assert (again.inserted, again.updated, again.unchanged, again.layer_id) == (0, 0, 7, first.layer_id)
    assert count(conn) == 7
    assert conn.execute(text("SELECT array_agg(road_id ORDER BY road_id) FROM road_segments WHERE layer_id = :l"),
                        {"l": first.layer_id}).scalar_one() == ids
    assert conn.execute(text("SELECT count(*) FROM spatial_layers WHERE layer_name = :n"), {"n": LAYER}).scalar_one() == 1


def test_changed_feature_is_updated_in_place(conn: Connection, insert_config: Callable[..., int]) -> None:
    spec = lv.SPECS["roads"]
    lv.load_layer(conn, roads(), spec, layer_name=LAYER)
    before = dict(conn.execute(text("SELECT osm_id, road_id FROM road_segments WHERE osm_id IN (-2, -3)")).all())
    config = insert_config()
    for road_id in before.values():
        conn.execute(text("INSERT INTO road_vulnerability (road_id, config_version, vulnerability_index, risk_category) "
                          "VALUES (:r, :c, 0.5, 'High')"), {"r": road_id, "c": config})
    changed = roads()
    changed.loc[changed.osm_id == -2, "surface"] = "gravel"                              # attribute only
    changed.loc[changed.osm_id == -3, "geometry"] = LineString([(X, Y + 900), (X + 70, Y + 950)])  # geometry
    result = lv.load_layer(conn, changed, spec, layer_name=LAYER)
    assert (result.inserted, result.updated, result.unchanged) == (0, 2, 5)
    assert result.results_invalidated == 1
    after = dict(conn.execute(text("SELECT osm_id, road_id FROM road_segments WHERE osm_id IN (-2, -3)")).all())
    assert after == before  # primary keys survive, so results keep pointing at the right road
    assert conn.execute(text("SELECT surface FROM road_segments WHERE osm_id = -2")).scalar_one() == "gravel"
    assert conn.execute(text("SELECT length_m FROM road_segments WHERE osm_id = -3")).scalar_one() \
        == pytest.approx(86.023, abs=1e-3)
    scored = conn.execute(text("SELECT array_agg(road_id) FROM road_vulnerability WHERE road_id = ANY(:ids)"),
                          {"ids": list(before.values())}).scalar_one()
    assert scored == [before[-2]]  # the road whose geometry moved lost its stale score


def test_rows_missing_from_the_source_are_kept_unless_pruned(conn: Connection) -> None:
    spec = lv.SPECS["roads"]
    lv.load_layer(conn, roads(6), spec, layer_name=LAYER)
    smaller = roads(4)
    kept = lv.load_layer(conn, smaller, spec, layer_name=LAYER)
    assert (kept.not_in_source, kept.pruned) == (2, 0) and count(conn) == 7
    pruned = lv.load_layer(conn, smaller, spec, layer_name=LAYER, prune=True)
    assert (pruned.not_in_source, pruned.pruned, pruned.rows_in_database) == (2, 2, 5) and count(conn) == 5


def test_pruning_never_touches_other_layers(conn: Connection, insert_road: Callable[..., int]) -> None:
    other = insert_road(name="hand-made row without an OSM id")
    lv.load_layer(conn, roads(), lv.SPECS["roads"], layer_name=LAYER, prune=True)
    assert conn.execute(text("SELECT count(*) FROM road_segments WHERE road_id = :r"), {"r": other}).scalar_one() == 1


def test_rivers_load_and_rerun(conn: Connection) -> None:
    spec = lv.SPECS["rivers"]
    first = lv.load_layer(conn, rivers(), spec, layer_name=LAYER)
    again = lv.load_layer(conn, rivers(), spec, layer_name=LAYER)
    assert (first.inserted, again.inserted, again.updated, again.unchanged) == (3, 0, 0, 3)
    assert count(conn, "river_centerlines") == 3
    assert conn.execute(text("SELECT array_agg(DISTINCT waterway) FROM river_centerlines WHERE layer_id = :l"),
                        {"l": first.layer_id}).scalar_one() == ["stream"]


def test_database_rejects_duplicate_source_keys(conn: Connection) -> None:
    from tests.db.conftest import expect_db_error

    lv.load_layer(conn, roads(), lv.SPECS["roads"], layer_name=LAYER)
    expect_db_error(conn, "INSERT INTO road_segments (osm_element, osm_id, part_no, highway_type, geom) "
                          "SELECT osm_element, osm_id, part_no, highway_type, geom FROM road_segments WHERE osm_id = -2")


def test_spatial_index_is_verified_and_recreated(conn: Connection) -> None:
    spec = lv.SPECS["roads"]
    conn.execute(text(f"DROP INDEX {spec.gist_index}"))
    lv.load_layer(conn, roads(), spec, layer_name=LAYER)
    assert conn.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
                        {"n": spec.gist_index}).scalar_one().lower().count("using gist") == 1


def test_verification_passes_then_detects_tampering(conn: Connection) -> None:
    spec, frame = lv.SPECS["roads"], roads()
    lv.load_layer(conn, frame, spec, layer_name=LAYER)
    good = lv.verify_layer(conn, frame, spec, layer_name=LAYER)
    assert good["ok"], good["errors"]
    s = good["stats"]
    assert s["database_features"] == s["source_features"] == 7 and s["srids"] == [32737]
    assert s["geometry_mismatches"] == s["attribute_mismatches"] == s["missing_in_database"] == 0
    assert all(q["within_500m"]["database"] == q["within_500m"]["source"] for q in s["spatial_queries"])

    conn.execute(text("UPDATE road_segments SET geom = ST_Translate(geom, 5, 0), name = 'edited' WHERE osm_id = -4"))
    conn.execute(text("DELETE FROM road_segments WHERE osm_id = -5"))
    bad = lv.verify_layer(conn, frame, spec, layer_name=LAYER)
    assert not bad["ok"]
    assert bad["stats"]["geometry_mismatches"] == 1 and bad["stats"]["attribute_mismatches"] == 1
    assert bad["stats"]["missing_in_database"] == 1


def test_verification_of_an_unloaded_layer_fails_cleanly(conn: Connection) -> None:
    result = lv.verify_layer(conn, roads(), lv.SPECS["roads"], layer_name="never_loaded")
    assert not result["ok"] and "not been loaded" in result["errors"][0]


def test_failed_load_file_rolls_back_everything(db_engine: Engine, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure after the upsert (here: the index check) must leave no row and no layer record."""
    path = tmp_path / "roads.gpkg"
    roads().rename(columns={"osm_element": "element", "highway_type": "highway"}).to_file(path, driver="GPKG")

    def boom(*_args, **_kwargs) -> None:
        raise lv.IngestionError("simulated failure after the rows were written")

    monkeypatch.setattr(lv, "ensure_spatial_index", boom)
    try:
        with pytest.raises(lv.IngestionError, match="simulated failure"):
            lv.load_file(path, "roads", db_engine, layer_name=LAYER)
        with db_engine.connect() as check:
            assert check.execute(text("SELECT count(*) FROM road_segments WHERE osm_id < 0")).scalar_one() == 0
            assert check.execute(text("SELECT count(*) FROM spatial_layers WHERE layer_name = :n"), {"n": LAYER}).scalar_one() == 0
    finally:  # only matters if the assertion above failed
        with db_engine.begin() as cleanup:
            cleanup.execute(text("DELETE FROM road_segments WHERE osm_id < 0"))
            cleanup.execute(text("DELETE FROM spatial_layers WHERE layer_name = :n"), {"n": LAYER})


def test_database_errors_are_reported_as_ingestion_errors(db_engine: Engine, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "roads.gpkg"
    roads().rename(columns={"osm_element": "element", "highway_type": "highway"}).to_file(path, driver="GPKG")
    monkeypatch.setattr(lv, "STAGE", "no such table name")  # makes the first SQL statement fail
    with pytest.raises(lv.IngestionError, match="rolled back"):
        lv.load_file(path, "roads", db_engine, layer_name=LAYER)
    with db_engine.connect() as check:
        assert check.execute(text("SELECT count(*) FROM spatial_layers WHERE layer_name = :n"), {"n": LAYER}).scalar_one() == 0
