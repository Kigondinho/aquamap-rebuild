"""Step 5: per-segment measurements, all computed in EPSG:32737 (metres).

* Terrain: every segment is sampled at points spaced at most ``step_m`` apart along the line (always
  including both ends and at least one interior point). Each point takes the value of the raster cell it
  falls in; nodata cells and points outside the raster are ignored. The segment's ``elevation_m`` and
  ``slope_pct`` are the means of its valid samples; a segment with no valid sample gets NaN (missing),
  never zero. How many samples were taken and how many were valid is kept next to the result.
* River distance: shortest planar distance in metres from the segment (the whole line, not its
  centroid) to the nearest river centreline; 0 where they cross.

``slope_pct`` is the slope of the terrain under the road (from the Phase 5 raster), not the gradient
of the carriageway.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core.config_defaults import STORAGE_SRID
from pipeline.errors import AcquisitionError

if TYPE_CHECKING:
    import geopandas as gpd
    import numpy as np

DEFAULT_STEP_M = 30.0  # the raster cell size: a finer step would only re-read the same cells
MIN_SAMPLES = 3        # start, middle, end


class MetricsError(AcquisitionError):
    """Inputs for the measurements are missing or inconsistent."""


@dataclass(frozen=True)
class RasterGrid:
    """A single-band raster held in memory, with nodata as NaN."""

    data: np.ndarray
    transform: object  # affine.Affine
    epsg: int | None

    @classmethod
    def open(cls, path: Path) -> RasterGrid:
        import numpy as np
        import rasterio

        if not Path(path).exists():
            raise MetricsError(f"raster not found: {path}; run 'python -m pipeline.prepare_dem'")
        with rasterio.open(path) as src:
            data = src.read(1).astype("float64")
            if src.nodata is not None:
                data[data == src.nodata] = np.nan
            data[~np.isfinite(data)] = np.nan
            return cls(data, src.transform, src.crs.to_epsg() if src.crs else None)

    def sample(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """Value of the cell containing each point; NaN for nodata and for points outside the raster."""
        import numpy as np

        t = self.transform
        cols = np.floor((np.asarray(xs) - t.c) / t.a).astype("int64")
        rows = np.floor((np.asarray(ys) - t.f) / t.e).astype("int64")
        inside = (rows >= 0) & (rows < self.data.shape[0]) & (cols >= 0) & (cols < self.data.shape[1])
        out = np.full(cols.shape, np.nan)
        out[inside] = self.data[rows[inside], cols[inside]]
        return out


def sample_counts(lengths: np.ndarray, step_m: float = DEFAULT_STEP_M) -> np.ndarray:
    """Number of sample points per segment: both ends plus interior points no more than ``step_m`` apart."""
    import numpy as np

    if step_m <= 0:
        raise ValueError("step_m must be positive")
    return np.maximum(MIN_SAMPLES, np.ceil(np.asarray(lengths, dtype="float64") / step_m).astype("int64") + 1)


def sample_points(geoms: np.ndarray, step_m: float = DEFAULT_STEP_M) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Evenly spaced points along each line. Returns (segment index of each point, x, y)."""
    import numpy as np
    import shapely

    counts = sample_counts(shapely.length(geoms), step_m)
    owner = np.repeat(np.arange(len(geoms)), counts)
    starts = np.repeat(np.cumsum(counts) - counts, counts)
    position = np.arange(counts.sum()) - starts            # 0 .. n-1 within each segment
    fraction = position / np.repeat(counts - 1, counts)    # 0.0 .. 1.0, ends included
    points = shapely.line_interpolate_point(geoms[owner], fraction, normalized=True)
    return owner, shapely.get_x(points), shapely.get_y(points)


def _mean_by_owner(values: np.ndarray, owner: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean of the valid (non-NaN) values per segment and the count of valid values. No valid value -> NaN."""
    import numpy as np

    valid = np.isfinite(values)
    count = np.bincount(owner[valid], minlength=n)
    total = np.bincount(owner[valid], weights=values[valid], minlength=n)
    mean = np.full(n, np.nan)
    np.divide(total, count, out=mean, where=count > 0)
    return mean, count


def _require_storage_crs(gdf: gpd.GeoDataFrame, what: str) -> None:
    if gdf.crs is None or gdf.crs.to_epsg() != STORAGE_SRID:
        raise MetricsError(f"{what} must be in EPSG:{STORAGE_SRID} (metres), got {gdf.crs}")


def add_length(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add ``length_m``."""
    _require_storage_crs(roads, "roads")
    roads = roads.copy()
    roads["length_m"] = roads.geometry.length
    return roads


def add_elevation_and_slope(roads: gpd.GeoDataFrame, dem_path: str | Path, slope_path: str | Path,
                            step_m: float = DEFAULT_STEP_M) -> gpd.GeoDataFrame:
    """Add ``elevation_m``, ``slope_pct`` (means of valid samples) and the sample counts."""
    import numpy as np

    _require_storage_crs(roads, "roads")
    dem, slope = RasterGrid.open(Path(dem_path)), RasterGrid.open(Path(slope_path))
    for name, grid in (("DEM", dem), ("slope raster", slope)):
        if grid.epsg != STORAGE_SRID:
            raise MetricsError(f"{name} is EPSG:{grid.epsg}, expected EPSG:{STORAGE_SRID}; run 'python -m pipeline.prepare_dem'")
    if dem.transform != slope.transform or dem.data.shape != slope.data.shape:
        raise MetricsError("elevation and slope rasters are not on the same grid; regenerate them together")

    roads = roads.copy()
    n = len(roads)
    if n == 0:
        for column in ("elevation_m", "slope_pct"):
            roads[column] = np.array([], dtype="float64")
        for column in ("terrain_samples", "elevation_samples_valid", "slope_samples_valid"):
            roads[column] = np.array([], dtype="int64")
        return roads
    owner, xs, ys = sample_points(roads.geometry.values, step_m)
    roads["elevation_m"], roads["elevation_samples_valid"] = _mean_by_owner(dem.sample(xs, ys), owner, n)
    roads["slope_pct"], roads["slope_samples_valid"] = _mean_by_owner(slope.sample(xs, ys), owner, n)
    roads["terrain_samples"] = np.bincount(owner, minlength=n)
    return roads


def add_river_distance(roads: gpd.GeoDataFrame, rivers: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add ``river_dist_m``: metres from each segment to the nearest river line (NaN if there are no rivers)."""
    import numpy as np
    import shapely

    _require_storage_crs(roads, "roads")
    roads = roads.copy()
    if len(rivers) == 0:
        roads["river_dist_m"] = np.nan
        return roads
    _require_storage_crs(rivers, "rivers")
    tree = shapely.STRtree(rivers.geometry.values)
    (road_idx, _river_idx), distance = tree.query_nearest(roads.geometry.values, return_distance=True, all_matches=False)
    out = np.full(len(roads), np.nan)
    out[road_idx] = distance
    roads["river_dist_m"] = out
    return roads
