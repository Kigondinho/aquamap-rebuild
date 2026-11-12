"""Terrain sampling and river distance on SYNTHETIC rasters and lines with known answers."""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
rasterio = pytest.importorskip("rasterio")
gpd = pytest.importorskip("geopandas")

from rasterio.transform import from_origin  # noqa: E402
from shapely.geometry import LineString  # noqa: E402

from core.ivi import ScoringConfig  # noqa: E402
from pipeline import compute_metrics as cm  # noqa: E402
from pipeline.score_roads import ScoringError, score_segments, summarise  # noqa: E402

X0, Y0, CELL, NODATA = 250000.0, 9860000.0, 30.0, -9999.0  # top-left corner of the synthetic grid


def write_raster(path: Path, data, *, epsg: int = 32737, x0: float = X0) -> Path:
    data = np.asarray(data, dtype="float32")
    with rasterio.open(path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1, dtype="float32",
                       crs=f"EPSG:{epsg}", transform=from_origin(x0, Y0, CELL, CELL), nodata=NODATA) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture
def rasters(tmp_path: Path) -> tuple[Path, Path]:
    """40x40 cells. Elevation rises 1 m per cell eastwards from 1500; slope is 4 % in the north half, 12 % south.
    Columns 30+ are nodata in both; the slope raster also has no value in its outer ring (as Phase 5 produces)."""
    rows, cols = np.mgrid[0:40, 0:40]
    elev = (1500.0 + cols).astype("float32")
    slope = np.where(rows < 20, 4.0, 12.0).astype("float32")
    elev[:, 30:] = NODATA
    slope[:, 30:] = NODATA
    slope[0, :] = slope[-1, :] = slope[:, 0] = NODATA
    return write_raster(tmp_path / "dem.tif", elev), write_raster(tmp_path / "slope.tif", slope)


def cell_xy(row: float, col: float) -> tuple[float, float]:
    """Centre of a cell (fractional indices allowed)."""
    return X0 + (col + 0.5) * CELL, Y0 - (row + 0.5) * CELL


def roads(*lines: LineString, surface: str | None = None, crs: int = 32737):
    return gpd.GeoDataFrame({"road_id": range(1, len(lines) + 1), "surface": [surface] * len(lines)},
                            geometry=list(lines), crs=crs)


# ---------------------------------------------------------------- sample layout
@pytest.mark.parametrize(("length", "expected"), [(0.5, 3), (29.0, 3), (30.0, 3), (60.0, 3), (61.0, 4), (300.0, 11), (305.0, 12)])
def test_sample_counts(length: float, expected: int) -> None:
    assert cm.sample_counts(np.array([length]))[0] == expected


def test_samples_are_evenly_spaced_and_include_both_ends() -> None:
    line = LineString([(0, 0), (300, 0)])
    owner, xs, ys = cm.sample_points(np.array([line, LineString([(0, 10), (0, 40)])], dtype=object))
    first = xs[owner == 0]
    assert len(first) == 11 and first[0] == 0 and first[-1] == 300
    assert np.allclose(np.diff(first), 30.0)
    assert list(ys[owner == 1]) == [10, 25, 40]  # short line: start, middle, end


def test_samples_follow_bends() -> None:
    line = LineString([(0, 0), (90, 0), (90, 90)])
    _, xs, ys = cm.sample_points(np.array([line], dtype=object))
    assert (xs[-1], ys[-1]) == (90, 90) and (90.0, 0.0) in set(zip(xs.tolist(), ys.tolist(), strict=True))
    assert np.diff(np.hypot(np.diff(xs), np.diff(ys))).max() < 1e-9  # equal spacing along the line


def test_sample_step_must_be_positive() -> None:
    with pytest.raises(ValueError):
        cm.sample_counts(np.array([10.0]), 0)


# ---------------------------------------------------------------- raster sampling
def test_raster_sample_reads_the_containing_cell(rasters: tuple[Path, Path]) -> None:
    grid = cm.RasterGrid.open(rasters[0])
    xs = np.array([cell_xy(5, 7)[0], X0 + 0.01, X0 + 7 * CELL + 29.9, X0 - 1, X0 + 35 * CELL])
    ys = np.array([cell_xy(5, 7)[1], Y0 - 0.01, Y0 - 100, Y0 - 100, Y0 - 100])
    out = grid.sample(xs, ys)
    assert out[0] == 1507 and out[1] == 1500 and out[2] == 1507
    assert np.isnan(out[3]) and np.isnan(out[4])  # outside the raster; nodata cell


def test_elevation_is_the_mean_along_the_whole_line(rasters: tuple[Path, Path]) -> None:
    """West-east line across columns 5..15: elevations 1505..1515, mean 1510, not the value at either end."""
    line = LineString([cell_xy(10, 5), cell_xy(10, 15)])
    out = cm.add_elevation_and_slope(roads(line), *rasters)
    assert out.terrain_samples[0] == 11 and out.elevation_samples_valid[0] == 11
    assert out.elevation_m[0] == pytest.approx(1510.0)
    assert out.slope_pct[0] == pytest.approx(4.0)


def test_slope_is_averaged_across_zones(rasters: tuple[Path, Path]) -> None:
    """North-south line with 5 cells at 4 % and 5 cells at 12 %."""
    line = LineString([cell_xy(15, 8), cell_xy(24, 8)])
    out = cm.add_elevation_and_slope(roads(line), *rasters)
    assert out.terrain_samples[0] == 10
    assert out.slope_pct[0] == pytest.approx(8.0)
    assert out.elevation_m[0] == pytest.approx(1508.0)


def test_nodata_samples_are_left_out_of_the_mean(rasters: tuple[Path, Path]) -> None:
    """Columns 25..34: only 25..29 have data, so the mean is 1527, not dragged down by -9999 or zeros."""
    line = LineString([cell_xy(10, 25), cell_xy(10, 34)])
    out = cm.add_elevation_and_slope(roads(line), *rasters)
    assert out.terrain_samples[0] == 10 and out.elevation_samples_valid[0] == 5
    assert out.elevation_m[0] == pytest.approx(1527.0)
    assert 1500 < out.elevation_m[0] < 1530


def test_segment_without_any_valid_sample_is_missing_not_zero(rasters: tuple[Path, Path]) -> None:
    in_void = LineString([cell_xy(10, 32), cell_xy(10, 38)])
    off_grid = LineString([(X0 - 5000, Y0), (X0 - 4000, Y0)])
    slope_ring_only = LineString([cell_xy(0, 3), cell_xy(0, 9)])  # elevation exists, slope does not
    out = cm.add_elevation_and_slope(roads(in_void, off_grid, slope_ring_only), *rasters)
    assert np.isnan(out.elevation_m[0]) and np.isnan(out.slope_pct[0])
    assert np.isnan(out.elevation_m[1]) and out.elevation_samples_valid[1] == 0 and out.terrain_samples[1] > 0
    assert out.elevation_m[2] == pytest.approx(1506.0) and np.isnan(out.slope_pct[2])

    scored = score_segments(cm.add_river_distance(out, roads(LineString([cell_xy(12, 0), cell_xy(12, 39)]))), ScoringConfig())
    assert scored.vulnerability_index.isna().all()
    assert scored.data_flags.tolist() == [["no_dem", "no_slope", "surface_missing"]] * 2 + [["no_slope", "surface_missing"]]
    assert scored.score_elevation[2] == 4 and np.isnan(scored.score_slope[2])


def test_very_short_segment_still_gets_three_samples(rasters: tuple[Path, Path]) -> None:
    x, y = cell_xy(10, 10)
    out = cm.add_elevation_and_slope(roads(LineString([(x, y), (x + 2, y)])), *rasters)
    assert out.terrain_samples[0] == 3 and out.elevation_m[0] == 1510


def test_rasters_must_match_each_other_and_the_storage_crs(rasters: tuple[Path, Path], tmp_path: Path) -> None:
    line = roads(LineString([cell_xy(10, 5), cell_xy(10, 15)]))
    shifted = write_raster(tmp_path / "shifted.tif", np.ones((40, 40)), x0=X0 + 15)
    with pytest.raises(cm.MetricsError, match="same grid"):
        cm.add_elevation_and_slope(line, rasters[0], shifted)
    north = write_raster(tmp_path / "north.tif", np.ones((40, 40)), epsg=32637)
    with pytest.raises(cm.MetricsError, match="EPSG:32637"):
        cm.add_elevation_and_slope(line, north, north)
    with pytest.raises(cm.MetricsError, match="not found"):
        cm.add_elevation_and_slope(line, tmp_path / "missing.tif", rasters[1])
    with pytest.raises(cm.MetricsError, match="EPSG:32737"):
        cm.add_elevation_and_slope(line.to_crs(4326), *rasters)


def test_empty_road_table_is_handled(rasters: tuple[Path, Path]) -> None:
    out = cm.add_elevation_and_slope(roads(), *rasters)
    assert len(out) == 0 and {"elevation_m", "slope_pct", "terrain_samples"} <= set(out.columns)


# ---------------------------------------------------------------- river distance
def test_river_distance_is_measured_from_the_whole_line_in_metres() -> None:
    river = roads(LineString([(0, 0), (1000, 0)]))
    parallel = LineString([(100, 40), (900, 40)])            # 40 m away along its whole length
    crossing = LineString([(500, -50), (500, 50)])           # crosses the river
    end_near = LineString([(200, 500), (200, 120)])          # centroid is 310 m away, nearest point 120 m
    beyond_end = LineString([(1300, 400), (1300, 800)])      # nearest river point is its end (1000, 0)
    out = cm.add_river_distance(roads(parallel, crossing, end_near, beyond_end), river)
    assert out.river_dist_m.tolist() == pytest.approx([40.0, 0.0, 120.0, 500.0])


def test_river_distance_picks_the_nearest_of_several_rivers() -> None:
    rivers = roads(LineString([(0, 0), (1000, 0)]), LineString([(0, 300), (1000, 300)]))
    out = cm.add_river_distance(roads(LineString([(100, 250), (900, 250)]), LineString([(100, 100), (900, 100)])), rivers)
    assert out.river_dist_m.tolist() == pytest.approx([50.0, 100.0])


def test_no_rivers_gives_missing_distance_and_a_flag() -> None:
    road = roads(LineString([(0, 0), (100, 0)]), surface="asphalt")
    out = cm.add_river_distance(road, roads())
    assert np.isnan(out.river_dist_m[0])
    out["elevation_m"], out["slope_pct"] = 1652.0, 5.2
    scored = score_segments(out, ScoringConfig())
    assert scored.score_river[0] == 1 and scored.data_flags[0] == ["no_river"] and scored.vulnerability_index[0] == 2.25


def test_degree_coordinates_are_refused_for_distance() -> None:
    lonlat = roads(LineString([(36.8, -1.3), (36.81, -1.3)]), crs=4326)
    with pytest.raises(cm.MetricsError, match="EPSG:32737"):
        cm.add_river_distance(lonlat, lonlat)
    with pytest.raises(cm.MetricsError):
        cm.add_length(lonlat)


def test_length_is_in_metres() -> None:
    assert cm.add_length(roads(LineString([(0, 0), (300, 400)]))).length_m[0] == 500.0


# ---------------------------------------------------------------- measured table -> scores
def test_measured_roads_are_scored_end_to_end(rasters: tuple[Path, Path]) -> None:
    river = roads(LineString([cell_xy(12, 0), cell_xy(12, 39)]))                 # west-east, along row 12
    near = LineString([cell_xy(11, 2), cell_xy(11, 8)])                          # 30 m from the river, elev 1505, slope 4
    far = LineString([cell_xy(35, 20), cell_xy(35, 28)])                         # 690 m away, elev 1524, slope 12
    frame = roads(near, far)
    frame["surface"] = ["dirt", "asphalt"]
    measured = cm.add_river_distance(cm.add_elevation_and_slope(frame, *rasters), river)
    assert measured.river_dist_m.tolist() == pytest.approx([30.0, 690.0])
    scored = score_segments(measured, ScoringConfig())
    # near: E 1505 -> 4, S 4 % -> 4, D 30 m -> 5, M dirt -> 5  => 1.40 + 0.80 + 1.50 + 0.75 = 4.45
    # far:  E 1524 -> 4, S 12 % -> 2, D 690 m -> 1, M asphalt -> 2 => 1.40 + 0.40 + 0.30 + 0.30 = 2.40
    assert scored.vulnerability_index.tolist() == [4.45, 2.4]
    assert scored.risk_category.tolist() == ["Severe", "Medium"]
    assert scored.data_flags.tolist() == [[], []]
    summary = summarise(scored)
    assert summary["segments_scored"] == 2 and summary["by_tier"]["Severe"]["segments"] == 1
    assert summary["segments_flagged"] == 0


def test_scoring_needs_the_measurement_columns() -> None:
    with pytest.raises(ScoringError, match="measurement step"):
        score_segments(roads(LineString([(0, 0), (1, 1)])), ScoringConfig())
