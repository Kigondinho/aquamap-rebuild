"""DEM preprocessing tests. All rasters are SYNTHETIC (generated here), used to prove the maths."""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
rasterio = pytest.importorskip("rasterio")

from rasterio.transform import from_origin  # noqa: E402

from pipeline import prepare_dem as pd  # noqa: E402
from pipeline.errors import AcquisitionError  # noqa: E402


def geo_dem(path: Path, *, plane=lambda yy, xx: 1500 + 0.0 * xx, size=(240, 300), void=None) -> Path:
    cell = 1 / 3600
    rows, cols = size
    yy, xx = np.mgrid[0:rows, 0:cols]
    data = plane(yy, xx).astype("int16")
    if void is not None:
        data[void(yy, xx)] = -32768
    with rasterio.open(path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="int16", crs="EPSG:4326",
                       transform=from_origin(36.80, -1.20, cell, cell), nodata=-32768) as dst:
        dst.write(data, 1)
    return path


# ---------------------------------------------------------------- slope maths
@pytest.mark.parametrize(("dzdx", "dzdy", "expected"), [(0.0, 0.0, 0.0), (0.1, 0.0, 10.0), (0.0, 0.1, 10.0),
                                                       (0.1, 0.05, 100 * (0.1**2 + 0.05**2) ** 0.5), (1.0, 0.0, 100.0)])
def test_horn_slope_on_planes_is_exact_percent(dzdx: float, dzdy: float, expected: float) -> None:
    yy, xx = np.mgrid[0:20, 0:20]
    z = 1500 + dzdx * 30 * xx + dzdy * 30 * yy  # 30 m cells; rows increase southwards
    s = pd.horn_slope_percent(z, 30, 30)
    inner = s[1:-1, 1:-1]
    assert np.allclose(inner, expected)
    assert np.isnan(s[0]).all() and np.isnan(s[-1]).all() and np.isnan(s[:, 0]).all() and np.isnan(s[:, -1]).all()


def test_slope_uses_cell_size() -> None:
    yy, xx = np.mgrid[0:10, 0:10]
    z = 10.0 * xx  # 10 m rise per cell
    assert np.allclose(pd.horn_slope_percent(z, 10, 10)[1:-1, 1:-1], 100.0)
    assert np.allclose(pd.horn_slope_percent(z, 30, 30)[1:-1, 1:-1], 100 / 3)


def test_nodata_never_creates_steep_slopes() -> None:
    yy, xx = np.mgrid[0:15, 0:15]
    z = (1500 + 0.0 * xx).astype("float64")
    z[7, 7] = np.nan
    z[0:3, 12:] = np.nan  # nodata block touching the edge
    s = pd.horn_slope_percent(z, 30, 30)
    assert np.nanmax(s) == 0.0  # flat terrain stays flat: no spike from the void or its borders
    assert np.isnan(s[6:9, 6:9]).all()  # the void and its 8 neighbours have no slope
    assert not np.isnan(s[10, 3])


def test_tiny_arrays() -> None:
    assert np.isnan(pd.horn_slope_percent(np.ones((2, 5)), 30, 30)).all()


# ---------------------------------------------------------------- reproject
def test_reproject_preserves_metadata_and_masks_voids(tmp_path: Path) -> None:
    src = geo_dem(tmp_path / "raw.tif", plane=lambda yy, xx: 1500 + xx, void=lambda yy, xx: (yy > 100) & (yy < 120) & (xx > 100) & (xx < 140))
    out = pd.reproject_dem(src, tmp_path / "dem.tif")
    with rasterio.open(out) as r:
        assert r.crs.to_epsg() == 32737 and r.res == (30.0, 30.0) and r.nodata == pd.OUT_NODATA
        assert r.dtypes[0] == "float32" and r.tags()["RESAMPLING"] == "bilinear"
        assert (r.transform.c / 30) % 1 == 0 and (r.transform.f / 30) % 1 == 0  # snapped grid
        z = r.read(1)
    valid = z != pd.OUT_NODATA
    assert (~valid).any()  # voids and rotated corners are nodata
    assert z[valid].min() >= 1500 - 1e-3 and z[valid].max() <= 1800  # no -32768 leaked into the data
    assert z[valid].min() > 0


def test_reproject_bilinear_keeps_plane_values(tmp_path: Path) -> None:
    src = geo_dem(tmp_path / "raw.tif", plane=lambda yy, xx: 1500 + xx)
    out = pd.reproject_dem(src, tmp_path / "dem.tif")
    with rasterio.open(out) as r:
        z = r.read(1)
        assert r.nodata == pd.OUT_NODATA
    v = z[z != pd.OUT_NODATA]
    assert v.min() >= 1499.9 and v.max() <= 1500 + 300  # inside the source value range, no overshoot


def test_reproject_errors(tmp_path: Path) -> None:
    with pytest.raises(AcquisitionError, match="not found"):
        pd.reproject_dem(tmp_path / "missing.tif", tmp_path / "o.tif")
    nocrs = tmp_path / "n.tif"
    with rasterio.open(nocrs, "w", driver="GTiff", height=5, width=5, count=1, dtype="int16") as dst:
        dst.write(np.ones((1, 5, 5), dtype="int16"))
    with pytest.raises(AcquisitionError, match="no CRS"):
        pd.reproject_dem(nocrs, tmp_path / "o.tif")
    with pytest.raises(AcquisitionError, match="projected"):
        pd.compute_slope(geo_dem(tmp_path / "g.tif"), tmp_path / "s.tif")


# ---------------------------------------------------------------- full run
def make_data_dir(tmp_path: Path, **kw) -> Path:
    raw = tmp_path / "data" / "raw" / "dem"
    raw.mkdir(parents=True)
    geo_dem(raw / "nairobi_srtm_30m.tif", plane=lambda yy, xx: 1500 + 0.5 * xx + 0.2 * yy, **kw)
    return tmp_path / "data"


def test_full_run_alignment_and_stats(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path, void=lambda yy, xx: (yy > 100) & (yy < 110) & (xx > 150) & (xx < 170))
    res = pd.run(data)
    assert res["ok"], res["errors"]
    with rasterio.open(data / pd.OUT_DEM) as d, rasterio.open(data / pd.OUT_SLOPE) as s:
        assert d.transform == s.transform and d.shape == s.shape and d.crs == s.crs
        e, sl = d.read(1), s.read(1)
    ok = sl != pd.OUT_NODATA
    assert (e[ok] != pd.OUT_NODATA).all()  # slope only where elevation exists
    # plane rises ~0.5 m per 30.9 m E-W and 0.2 m per 30.9 m N-S: slope ~ 100*hypot(.5,.2)/30.9 percent
    expected = 100 * np.hypot(0.5, 0.2) / 30.9
    centre = sl[ok]
    assert abs(float(np.median(centre)) - expected) < 0.2
    assert centre.max() < expected * 1.2  # no spikes around the void or raster corners
    assert (data / "processed" / "dem_32737.tif.provenance.json").exists()
    assert (data / pd.OUT_REPORT).exists()


def test_regeneration_is_repeatable(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    pd.run(data)
    with rasterio.open(data / pd.OUT_SLOPE) as s:
        first = s.read(1)
    pd.run(data, force=True)
    with rasterio.open(data / pd.OUT_SLOPE) as s:
        assert np.array_equal(first, s.read(1))


# ---------------------------------------------------------------- validation catches problems
def test_validation_detects_problems(tmp_path: Path) -> None:
    data = make_data_dir(tmp_path)
    pd.run(data)
    dem, slope = data / pd.OUT_DEM, data / pd.OUT_SLOPE
    # misaligned slope
    bad = tmp_path / "shifted.tif"
    with rasterio.open(slope) as s:
        prof = s.profile
        arr = s.read(1)
    prof["transform"] = from_origin(prof["transform"].c + 30, prof["transform"].f, 30, 30)
    with rasterio.open(bad, "w", **prof) as dst:
        dst.write(arr, 1)
    assert any("aligned" in e for e in pd.validate_terrain(dem, bad)["errors"])
    # slope in degrees-like small numbers / ratio
    ratio = tmp_path / "ratio.tif"
    with rasterio.open(ratio, "w", **prof | {"transform": from_origin(*(lambda t: (t.c, t.f))(rasterio.open(slope).transform), 30, 30)}) as dst:
        dst.write(np.where(arr == pd.OUT_NODATA, pd.OUT_NODATA, arr / 100).astype("float32"), 1)
    assert any("not percent" in e for e in pd.validate_terrain(dem, ratio)["errors"])
    # slope present next to nodata
    holey = tmp_path / "holey.tif"
    full = np.where(arr == pd.OUT_NODATA, 5.0, arr).astype("float32")
    with rasterio.open(holey, "w", **prof | {"transform": rasterio.open(slope).transform}) as dst:
        dst.write(full, 1)
    assert any("nodata/edge" in e for e in pd.validate_terrain(dem, holey)["errors"])
    # wrong resolution requested
    assert any("cell size" in e for e in pd.validate_terrain(dem, slope, resolution=10)["errors"])


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = make_data_dir(tmp_path)
    assert pd.main(["--data-dir", str(data)]) == 0
    assert "PASS" in capsys.readouterr().out
    assert pd.main(["--data-dir", str(tmp_path / "empty"), "--force"]) == 1
    assert "FAILED" in capsys.readouterr().err
