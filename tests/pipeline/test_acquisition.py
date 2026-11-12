"""Acquisition, validation and provenance tests.

ALL DATA HERE IS SYNTHETIC (generated in the tests, never written to ``data/``). It exists only to
prove the code detects problems; it says nothing about real Nairobi data. The network is mocked.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

gpd = pytest.importorskip("geopandas")
rasterio = pytest.importorskip("rasterio")
np = pytest.importorskip("numpy")
pytest.importorskip("requests")

from rasterio.transform import from_origin  # noqa: E402
from shapely.geometry import LineString, Point, box  # noqa: E402

from pipeline import acquire, fetch_data as fd, validate as v  # noqa: E402
from pipeline.errors import AcquisitionError, SourceUnavailable, ValidationFailed  # noqa: E402
from pipeline.process_vectors import process_vector  # noqa: E402
from pipeline.provenance import read_provenance, verify_checksums  # noqa: E402

W, S, E, N = 36.70, -1.40, 37.02, -1.22  # synthetic "county" of about 710 km2


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fd.time, "sleep", lambda *_: None)


def county() -> Any:
    return gpd.GeoDataFrame({"name": ["Synthetic County"]}, geometry=[box(W, S, E, N)], crs=4326)


def road_grid(step: float = 0.004, crs: int = 4326, with_ids: bool = True) -> Any:
    lines = [LineString([(W, y), (E, y)]) for y in np.arange(S, N, step)]
    lines += [LineString([(x, S), (x, N)]) for x in np.arange(W, E, step)]
    n = len(lines)
    data: dict[str, Any] = {"highway": ["residential"] * n, "name": [None] * n, "surface": [None] * n}
    if with_ids:
        data["element"] = ["way"] * n
        data["id"] = list(range(1, n + 1))
    return gpd.GeoDataFrame(data, geometry=lines, crs=4326).to_crs(crs)


def rivers() -> Any:
    lines = [LineString([(W + i * 0.01, S), (W + i * 0.01 + 0.02, N)]) for i in range(10)]
    return gpd.GeoDataFrame({"element": ["way"] * 10, "id": range(100, 110), "waterway": ["stream"] * 10,
                             "name": [None] * 10}, geometry=lines, crs=4326)


def checks(rep: v.Report) -> set[str]:
    return {i.check for i in rep.errors}


def write_dem(path: Path, *, bounds=(W - 0.03, S - 0.03, E + 0.03, N + 0.03), cell=1 / 3600, crs="EPSG:4326",
              fill=None, nodata=-32768) -> Path:
    w, s, e, n = bounds
    cols, rows = round((e - w) / cell), round((n - s) / cell)
    yy, xx = np.mgrid[0:rows, 0:cols]
    data = (1500 + xx * 0.1 + yy * 0.05).astype("int16")
    if fill is not None:
        data[fill(yy, xx)] = nodata
    profile = dict(driver="GTiff", height=rows, width=cols, count=1, dtype="int16", crs=crs,
                   transform=from_origin(w, n, cell, cell), nodata=nodata)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
    return path


# ------------------------------------------------------------------ boundary
def test_boundary_ok_and_wrong_area() -> None:
    assert v.validate_boundary(county()).ok
    tiny = gpd.GeoDataFrame(geometry=[box(36.8, -1.3, 36.85, -1.28)], crs=4326)
    assert "area" in checks(v.validate_boundary(tiny))
    elsewhere = gpd.GeoDataFrame(geometry=[box(39.0, -4.0, 39.3, -3.8)], crs=4326)
    assert "location" in checks(v.validate_boundary(elsewhere))


# ------------------------------------------------------------------ vectors
def test_good_roads_pass_with_expected_stats() -> None:
    rep = v.validate_vector(road_grid(), "roads", expected_epsg=4326, boundary=county(), min_features=50)
    assert rep.ok, rep.errors
    assert rep.stats["geometry_types"] == {"LineString": rep.stats["feature_count"]}
    assert rep.stats["grid_cells_with_road_pct"] > 95
    assert any(i.check == "attributes" and "surface" in i.message for i in rep.issues)  # missing surface reported


def test_crs_errors() -> None:
    gdf = road_grid()
    assert "crs" in checks(v.validate_vector(gdf, "roads", expected_epsg=32737, min_features=10))
    assert "crs" in checks(v.validate_vector(gdf.set_crs(None, allow_override=True), "roads", expected_epsg=4326, min_features=10))


def test_geometry_problems_detected() -> None:
    gdf = road_grid()
    extra = gpd.GeoDataFrame(
        {"highway": ["x"] * 4, "element": ["way"] * 4, "id": [9001, 9002, 9003, 9004]},
        geometry=[Point(37, -1.3), LineString(), None, LineString([(37, -1.3), (37, -1.3)])], crs=4326)
    rep = v.validate_vector(gpd.pd.concat([gdf, extra], ignore_index=True), "roads", expected_epsg=4326, min_features=10)
    assert {"geometry_types", "empty_null_invalid"} <= checks(rep)
    assert rep.stats["null_geometries"] == 1 and rep.stats["empty_geometries"] == 1
    assert rep.stats["invalid_geometries"] >= 1


def test_duplicates_detected() -> None:
    gdf = road_grid()
    dup = gpd.pd.concat([gdf, gdf.iloc[:3]], ignore_index=True)
    rep = v.validate_vector(dup, "roads", expected_epsg=4326, min_features=10)
    assert "duplicates" in checks(rep)
    assert rep.stats["duplicate_ids"] == 3 and rep.stats["duplicate_geometries"] == 3
    # same geometry, different id: only a warning
    g2 = gdf.copy()
    g2.loc[g2.index[0], "geometry"] = g2.geometry.iloc[1]
    rep2 = v.validate_vector(g2, "roads", expected_epsg=4326, min_features=10)
    assert rep2.ok and rep2.warnings


def test_missing_attributes() -> None:
    gdf = road_grid()
    assert "attributes" in checks(v.validate_vector(gdf.drop(columns="highway"), "roads", expected_epsg=4326, min_features=10))
    gdf.loc[gdf.index[:5], "highway"] = None
    rep = v.validate_vector(gdf, "roads", expected_epsg=4326, min_features=10)
    assert "attributes" in checks(rep) and rep.stats["highway_null"] == 5
    assert "attributes" in checks(v.validate_vector(rivers().drop(columns="waterway"), "rivers", expected_epsg=4326, min_features=5))


def test_completeness_and_coverage() -> None:
    gdf = road_grid()
    assert "completeness" in checks(v.validate_vector(gdf, "roads", expected_epsg=4326, min_features=10**6))
    assert "completeness" in checks(v.validate_vector(gdf.iloc[:0], "roads", expected_epsg=4326))
    # a truncated download: only the western third of the county
    west = gdf[gdf.geometry.bounds.maxx <= W + 0.1]
    rep = v.validate_vector(west, "roads", expected_epsg=4326, boundary=county(), min_features=5)
    assert "completeness" in checks(rep)
    # data for the wrong city
    moved = gdf.copy()
    moved["geometry"] = moved.geometry.translate(xoff=2.0)
    assert "coverage" in checks(v.validate_vector(moved, "roads", expected_epsg=4326, boundary=county(), min_features=10))


def test_projected_layer_validates() -> None:
    rep = v.validate_vector(road_grid(crs=32737), "roads", expected_epsg=32737, boundary=county(), min_features=10)
    assert rep.ok, rep.errors


def test_rivers_pass() -> None:
    assert v.validate_vector(rivers(), "rivers", expected_epsg=4326, boundary=county(), min_features=5).ok


# ------------------------------------------------------------------ DEM
def test_dem_ok(tmp_path: Path) -> None:
    rep = v.validate_dem(write_dem(tmp_path / "d.tif"), county())
    assert rep.ok, rep.errors
    assert 25 < rep.stats["approx_cell_m"] < 35 and rep.stats["void_pct"] == 0


def test_dem_failures(tmp_path: Path) -> None:
    short = write_dem(tmp_path / "short.tif", bounds=(W, S, E - 0.05, N))
    assert "coverage" in checks(v.validate_dem(short, county()))
    coarse = write_dem(tmp_path / "coarse.tif", cell=0.0025)
    assert "resolution" in checks(v.validate_dem(coarse, county()))
    voids = write_dem(tmp_path / "void.tif", fill=lambda yy, xx: xx % 2 == 0)
    assert "nodata" in checks(v.validate_dem(voids, county()))
    wrong = write_dem(tmp_path / "range.tif")
    with rasterio.open(wrong, "r+") as ds:
        ds.write(np.full((ds.height, ds.width), 9000, dtype="int16"), 1)
    assert "value_range" in checks(v.validate_dem(wrong, county()))
    assert "readable" in checks(v.validate_dem(tmp_path / "missing.tif"))


# ------------------------------------------------------------------ processing + provenance
def test_process_vector_cleans_and_records(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "osm" / "r.gpkg"
    raw.parent.mkdir(parents=True)
    gdf = road_grid().head(30)
    gdf.loc[gdf.index[1], "id"] = gdf.loc[gdf.index[0], "id"]  # duplicate id
    gdf["extra_tag"] = "kept only in raw"
    gdf.to_file(raw, driver="GPKG")
    out = process_vector(raw, "roads", tmp_path / "processed", tmp_path)
    res = gpd.read_file(out)
    assert res.crs.to_epsg() == 32737 and len(res) == 29
    assert "extra_tag" not in res.columns and {"osm_id", "highway"} <= set(res.columns)
    rec = read_provenance(out)
    assert rec["stage"] == "processed" and rec["processing_log"]["dropped_duplicate_ids"] == 1
    assert gpd.read_file(raw).crs.to_epsg() == 4326 and len(gpd.read_file(raw)) == 30  # raw untouched
    assert verify_checksums(out) == []
    out.write_bytes(out.read_bytes() + b"x")
    assert verify_checksums(out)


def test_process_missing_raw(tmp_path: Path) -> None:
    with pytest.raises(AcquisitionError):
        process_vector(tmp_path / "nope.gpkg", "roads", tmp_path)


# ------------------------------------------------------------------ OSM fetch (mocked OSMnx)
class FakeOx:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result, self.error, self.calls = result, error, []

    def features_from_polygon(self, polygon: Any, tags: Any) -> Any:
        self.calls.append(tags)
        if self.error:
            raise self.error
        return self.result

    def geocode_to_gdf(self, query: str) -> Any:
        if self.error:
            raise self.error
        return self.result


def osm_result() -> Any:
    g = road_grid(with_ids=False).head(20)
    g["element"] = "way"
    g["id"] = range(1, 21)
    g["lanes"] = [["2", "3"]] + [None] * 19  # list values must not break GeoPackage writing
    pt = gpd.GeoDataFrame({"element": ["node"], "id": [99], "highway": ["bus_stop"]}, geometry=[Point(37, -1.3)], crs=4326)
    return gpd.pd.concat([g, pt]).set_index(["element", "id"], drop=False).rename_axis(["e", "i"])


def raw_with_boundary(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    (raw / "boundary").mkdir(parents=True)
    county().to_file(raw / fd.RAW_BOUNDARY, driver="GPKG")
    return raw


def test_fetch_roads_saves_raw_and_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = raw_with_boundary(tmp_path)
    fake = FakeOx(osm_result())
    monkeypatch.setattr(fd, "_configure_osmnx", lambda cache: fake)
    out = fd.fetch_roads(raw, tmp_path / "cache")
    saved = gpd.read_file(out)
    assert len(saved) == 20 and set(saved.geom_type) == {"LineString"}
    rec = read_provenance(out)
    assert rec["parameters"]["tags"] == {"highway": True}
    assert rec["parameters"]["non_line_features_not_saved"] == {"Point": 1}
    assert "ODbL" in rec["licence"] and "OpenStreetMap contributors" in rec["attribution"]
    assert rec["retrieved_at"] and rec["files"][0]["sha256"] and rec["limitations"]
    # second call reuses the file instead of downloading again; --force downloads again
    fd.fetch_roads(raw, tmp_path / "cache")
    assert len(fake.calls) == 1
    fd.fetch_roads(raw, tmp_path / "cache", force=True)
    assert len(fake.calls) == 2


def test_fetch_failures_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = raw_with_boundary(tmp_path)
    monkeypatch.setattr(fd, "_configure_osmnx", lambda cache: FakeOx(error=ConnectionError("blocked")))
    with pytest.raises(SourceUnavailable, match="blocked"):
        fd.fetch_rivers(raw, tmp_path / "c")
    assert not (raw / fd.RAW_RIVERS).exists()
    monkeypatch.setattr(fd, "_configure_osmnx", lambda cache: FakeOx(result=gpd.GeoDataFrame(geometry=[Point(0, 0)], crs=4326)))
    with pytest.raises(AcquisitionError, match="no line features"):
        fd.fetch_rivers(raw, tmp_path / "c")
    with pytest.raises(AcquisitionError, match="boundary missing"):
        fd.fetch_roads(tmp_path / "empty", tmp_path / "c")


def test_boundary_rejects_wrong_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wrong = gpd.GeoDataFrame({"name": ["Mombasa"]}, geometry=[box(39.5, -4.1, 39.8, -3.9)], crs=4326)
    monkeypatch.setattr(fd, "_configure_osmnx", lambda cache: FakeOx(wrong))
    with pytest.raises(ValidationFailed):
        fd.fetch_boundary(tmp_path / "raw", tmp_path / "c")
    f = tmp_path / "b.gpkg"
    county().to_file(f, driver="GPKG")
    out = fd.fetch_boundary(tmp_path / "raw", tmp_path / "c", boundary_file=f)
    assert read_provenance(out)["method"] == "local file"


# ------------------------------------------------------------------ DEM fetch (mocked HTTP)
def test_tile_names_for_nairobi() -> None:
    assert fd.tile_names(36.65, -1.45, 37.10, -1.15) == ["S02E036", "S02E037"]
    assert fd.tile_names(-0.5, 0.5, 0.5, 1.5) == ["N00W001", "N00E000", "N01W001", "N01E000"]
    assert fd.tile_url("S02E036").endswith("/skadi/S02/S02E036.hgt.gz")


def hgt_bytes(lon0: int) -> bytes:
    yy, xx = np.mgrid[0:3601, 0:3601]
    arr = (1400 + (lon0 - 36) * 3601 * 0.0 + xx * 0.2 + yy * 0.1).astype(">i2")
    return gzip.compress(arr.tobytes(), 1)


class FakeResp:
    def __init__(self, body: bytes, status: int = 200, length: int | None = None) -> None:
        self.body, self.status_code = body, status
        self.headers = {"Content-Length": str(len(body) if length is None else length)}

    def __enter__(self) -> FakeResp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, n: int):  # noqa: ANN201
        yield self.body


class FakeSession:
    def __init__(self, handler) -> None:  # noqa: ANN001
        self.handler, self.urls = handler, []

    def get(self, url: str, **kw: Any) -> FakeResp:
        self.urls.append(url)
        return self.handler(url)


def test_fetch_dem_mosaic_and_provenance(tmp_path: Path) -> None:
    raw = raw_with_boundary(tmp_path)
    bodies = {"S02E036": hgt_bytes(36), "S02E037": hgt_bytes(37)}
    session = FakeSession(lambda url: FakeResp(bodies[url.rsplit("/", 1)[1].split(".")[0]]))
    out = fd.fetch_dem(raw, boundary_path=raw / fd.RAW_BOUNDARY, session=session)
    assert len(session.urls) == 2
    with rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 4326 and ds.nodata == -32768 and ds.count == 1
        assert ds.bounds.left <= W and ds.bounds.right >= E and ds.bounds.bottom <= S and ds.bounds.top >= N
    rec = read_provenance(out)
    assert rec["parameters"]["tiles"] == ["S02E036", "S02E037"]
    assert "public-domain" in rec["licence"] and "U.S. Geological Survey" in rec["attribution"]
    assert (raw / "dem" / "tiles" / "S02E036.hgt.gz").exists() and not list((raw / "dem" / "tiles").glob("*.hgt"))
    assert verify_checksums(out) == []


def test_fetch_dem_failures(tmp_path: Path) -> None:
    raw = raw_with_boundary(tmp_path)
    notfound = FakeSession(lambda url: FakeResp(b"", status=404))
    with pytest.raises(AcquisitionError, match="404"):
        fd.fetch_dem(raw, boundary_path=raw / fd.RAW_BOUNDARY, session=notfound)

    def broken(url: str) -> FakeResp:
        raise ConnectionError("reset")

    with pytest.raises(SourceUnavailable, match="reset"):
        fd.fetch_dem(raw, boundary_path=raw / fd.RAW_BOUNDARY, session=FakeSession(broken))
    truncated = FakeSession(lambda url: FakeResp(b"abc", length=1000))
    with pytest.raises(SourceUnavailable, match="truncated"):
        fd.fetch_dem(raw, boundary_path=raw / fd.RAW_BOUNDARY, session=truncated)
    assert not list((raw / "dem").rglob("*.part"))  # no half-written files left behind
    garbage = FakeSession(lambda url: FakeResp(b"not gzip data"))
    with pytest.raises(AcquisitionError, match="gzip"):
        fd.fetch_dem(raw, boundary_path=raw / fd.RAW_BOUNDARY, session=garbage)
    assert not list((raw / "dem").rglob("*.hgt.gz"))
    assert not (raw / fd.DEM_MOSAIC).exists()


def test_user_supplied_dem_requires_source(tmp_path: Path) -> None:
    src = write_dem(tmp_path / "mine.tif")
    raw = raw_with_boundary(tmp_path)
    with pytest.raises(AcquisitionError, match="dem-source"):
        fd.fetch_dem(raw, dem_file=src)
    out = fd.fetch_dem(raw, boundary_path=raw / fd.RAW_BOUNDARY, dem_file=src,
                       dem_source="USGS EarthExplorer SRTM 1 Arc-Second Global")
    assert read_provenance(out)["method"] == "user-supplied file"


# ------------------------------------------------------------------ CLI
def test_cli_reports_failures_and_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(fd, "_configure_osmnx", lambda cache: FakeOx(error=ConnectionError("no route")))
    assert acquire.main(["--data-dir", str(tmp_path), "fetch", "roads"]) == 1  # roads: boundary missing
    err = capsys.readouterr().err
    assert "FAILURES" in err and "boundary" in err
    assert acquire.main(["--data-dir", str(tmp_path), "fetch", "boundary"]) == 1
    assert "no route" in capsys.readouterr().err


def test_cli_validate_complete_synthetic_dataset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = raw_with_boundary(tmp_path)
    (raw / "osm").mkdir()
    (raw / "rivers").mkdir()
    big = road_grid().assign(element="way")
    big.to_file(raw / fd.RAW_ROADS, driver="GPKG")
    rivers().to_file(raw / fd.RAW_RIVERS, driver="GPKG")
    write_dem(raw / "dem" / "nairobi_srtm_30m.tif") if (raw / "dem").mkdir() is None else None
    reports = acquire.validate_all(tmp_path)
    by = {r.dataset: r for r in reports}
    assert by["dem"].ok and by["boundary"].ok
    assert "roads (processed)" in by and not by["roads (processed)"].ok  # not produced yet
    assert acquire.main(["--data-dir", str(tmp_path), "validate"]) == 1
    saved = json.loads((tmp_path / "processed" / "validation_report.json").read_text())
    assert saved["all_ok"] is False
