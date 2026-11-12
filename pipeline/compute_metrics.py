"""Step 5: per-segment metrics computed in EPSG:32737 (metres)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import geopandas as gpd


def add_length(roads: "gpd.GeoDataFrame") -> "gpd.GeoDataFrame":
    """Add ``length_m``."""
    raise NotImplementedError("segment length")


def add_elevation_and_slope(
    roads: "gpd.GeoDataFrame", dem_path: str, slope_path: str, step_m: float = 30.0
) -> "gpd.GeoDataFrame":
    """Sample along each line every ``step_m`` metres; add mean ``elevation_m`` and ``slope_pct``."""
    raise NotImplementedError("raster sampling")


def add_river_distance(roads: "gpd.GeoDataFrame", rivers: "gpd.GeoDataFrame") -> "gpd.GeoDataFrame":
    """Add ``river_dist_m`` using a spatial nearest join."""
    raise NotImplementedError("river distance")
