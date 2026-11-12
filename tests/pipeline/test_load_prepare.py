"""Cleaning that happens before ingestion (no database needed). All geometries are SYNTHETIC."""

from __future__ import annotations

import pytest

gpd = pytest.importorskip("geopandas")
shapely = pytest.importorskip("shapely")

from shapely.geometry import GeometryCollection, LineString, MultiLineString, Point, Polygon  # noqa: E402

from pipeline.geometry import clean_lines, line_parts, repair_line  # noqa: E402
from pipeline.load_vectors import SPECS, IngestionError, prepare  # noqa: E402

X, Y = 257000.0, 9857000.0  # central Nairobi in EPSG:32737


def line(dx: float = 0.0, n: float = 100.0) -> LineString:
    return LineString([(X + dx, Y), (X + dx, Y + n)])


def frame(rows: list[dict], crs: int | None = 32737) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


# ---------------------------------------------------------------- geometry helpers
def test_degenerate_line_is_invalid_and_unrepairable() -> None:
    bad = LineString([(X, Y), (X, Y)])
    assert not bad.is_valid
    assert repair_line(bad) is None


def test_line_parts_keeps_only_line_work() -> None:
    mixed = GeometryCollection([Point(X, Y), line(), Polygon([(0, 0), (1, 0), (1, 1)])])
    assert line_parts(mixed).equals(line())
    assert line_parts(Point(X, Y)) is None
    assert line_parts(MultiLineString([line(), line(50)])).geom_type == "MultiLineString"


def test_clean_lines_counts_every_change() -> None:
    gdf = frame([
        {"k": "ok", "geometry": line()},
        {"k": "null", "geometry": None},
        {"k": "empty", "geometry": LineString()},
        {"k": "point", "geometry": Point(X, Y)},
        {"k": "degenerate", "geometry": LineString([(X, Y), (X, Y)])},
        {"k": "multi", "geometry": MultiLineString([line(10), line(20), line(30)])},
    ])
    out, log = clean_lines(gdf)
    assert log["input_features"] == 6
    assert log["dropped_null_or_empty"] == 2 and log["dropped_non_line"] == 1
    assert log["invalid_found"] == 1 and log["repaired_invalid"] == 0 and log["dropped_unrepairable"] == 1
    assert log["multilines_split_extra_parts"] == 2 and log["output_features"] == 4
    assert set(out.geom_type) == {"LineString"} and out.geometry.is_valid.all() and not out.geometry.is_empty.any()
    assert list(out["k"]) == ["ok", "multi", "multi", "multi"]  # attributes follow their parts, in order


def test_clean_lines_repairs_a_multiline_with_one_bad_part() -> None:
    gdf = frame([{"k": "a", "geometry": MultiLineString([line(), LineString([(X, Y), (X, Y)])])}])
    assert not gdf.geometry.is_valid.all()
    out, log = clean_lines(gdf)
    assert log["repaired_invalid"] == 1 and log["dropped_unrepairable"] == 0
    assert len(out) == 1 and out.geometry.iloc[0].equals(line())


# ---------------------------------------------------------------- prepare
def test_prepare_reprojects_lonlat_to_32737_and_maps_attributes() -> None:
    src = frame([{"element": "way", "osm_id": 1, "name": " Moi Avenue ", "highway": "primary", "surface": None,
                  "geometry": LineString([(36.82, -1.29), (36.83, -1.29)])}], crs=4326)
    out, log = prepare(src, SPECS["roads"])
    assert out.crs.to_epsg() == 32737 and log["reprojected_to"] == "EPSG:32737"
    assert list(out.columns) == ["osm_element", "osm_id", "part_no", "name", "highway_type", "surface", "geometry"]
    row = out.iloc[0]
    assert (row.osm_element, row.osm_id, row.part_no, row["name"], row.highway_type) == ("way", 1, 0, "Moi Avenue", "primary")
    assert row.surface is None
    assert 1100 < row.geometry.length < 1125  # 0.01 degrees of longitude at Nairobi, in metres


def test_prepare_numbers_parts_and_drops_bad_rows() -> None:
    src = frame([
        {"element": "way", "osm_id": 1, "name": None, "highway": "residential", "geometry": MultiLineString([line(), line(40)])},
        {"element": "way", "osm_id": 1, "name": "repeat", "highway": "residential", "geometry": line(40)},   # exact repeat
        {"element": "way", "osm_id": 1, "name": None, "highway": "residential", "geometry": line(80)},       # another part
        {"element": "way", "osm_id": 2, "name": None, "highway": None, "geometry": line(120)},               # no highway
        {"element": "way", "osm_id": None, "name": None, "highway": "service", "geometry": line(160)},       # no id
        {"element": "relation", "osm_id": 1, "name": "x" * 300, "highway": "service", "geometry": line(200)},
    ])
    out, log = prepare(src, SPECS["roads"])
    assert log["dropped_duplicate_records"] == 1 and log["dropped_no_osm_id"] == 1
    assert log["dropped_missing_required_attribute"] == 1 and log["attribute_values_truncated"] == 1
    keys = list(zip(out.osm_element, out.osm_id, out.part_no, strict=True))
    assert keys == [("way", 1, 0), ("way", 1, 1), ("way", 1, 2), ("relation", 1, 0)]
    assert len(set(keys)) == len(keys)
    assert len(out["name"].iloc[3]) == 255
    assert out["name"].iloc[0] is None  # missing stays None, never NaN or the text 'nan'


def test_prepare_is_repeatable() -> None:
    src = frame([{"osm_id": i, "highway": "service", "geometry": MultiLineString([line(i * 10), line(i * 10 + 5)])} for i in range(5)])
    a, _ = prepare(src, SPECS["roads"])
    b, _ = prepare(src, SPECS["roads"])
    assert a.drop(columns="geometry").equals(b.drop(columns="geometry")) and a.geometry.equals(b.geometry)


def test_prepare_drops_z_coordinates() -> None:
    src = frame([{"osm_id": 1, "waterway": "stream", "geometry": LineString([(X, Y, 1600.0), (X + 50, Y, 1601.0)])}])
    out, _ = prepare(src, SPECS["rivers"])
    assert not out.geometry.has_z.any()


def test_prepare_refuses_missing_crs_or_missing_id_column() -> None:
    with pytest.raises(IngestionError, match="no CRS"):
        prepare(frame([{"osm_id": 1, "highway": "service", "geometry": line()}], crs=None), SPECS["roads"])
    with pytest.raises(IngestionError, match="osm_id"):
        prepare(frame([{"highway": "service", "geometry": line()}]), SPECS["roads"])
