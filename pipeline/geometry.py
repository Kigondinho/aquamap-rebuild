"""Line-geometry clean-up shared by vector processing (Phase 4) and database ingestion (Phase 6).

``clean_lines`` validates every geometry, repairs what can be repaired and removes what cannot be
stored as a ``LineString``. Nothing is invented: a repair only keeps line work that was already in
the feature, and every removal is counted in the returned log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import geopandas as gpd
    from shapely.geometry.base import BaseGeometry

LINE_TYPES = ("LineString", "MultiLineString")


def line_parts(geom: BaseGeometry | None) -> BaseGeometry | None:
    """The usable line work of a geometry as a LineString/MultiLineString, or None if there is none.

    A part is usable when it is a non-empty, valid LineString with a length above zero.
    """
    import shapely
    from shapely.geometry import MultiLineString

    if geom is None or geom.is_empty:
        return None
    parts = [p for p in shapely.get_parts(shapely.get_parts(geom)).tolist() if p.geom_type == "LineString"] \
        if geom.geom_type == "GeometryCollection" else \
        [p for p in shapely.get_parts(geom).tolist() if p.geom_type == "LineString"]
    parts = [p for p in parts if not p.is_empty and p.is_valid and p.length > 0]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else MultiLineString(parts)


def repair_line(geom: BaseGeometry) -> BaseGeometry | None:
    """Repair an invalid line geometry with ``make_valid`` and keep its line parts (None = unrepairable)."""
    import shapely

    try:
        return line_parts(shapely.make_valid(geom))
    except shapely.errors.GEOSException:
        return None


def clean_lines(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Return a frame of valid, non-empty, single-part LineStrings and a log of what was changed.

    Steps: drop null/empty geometries; drop non-line geometry types; repair invalid lines (drop the
    ones that cannot be repaired); split MultiLineStrings into parts (file order is kept, so part
    numbering is repeatable); drop zero-length parts. Attributes are untouched.
    """
    import shapely

    log: dict[str, Any] = {"input_features": int(len(gdf))}

    values = gdf.geometry.values
    ok = ~(shapely.is_missing(values) | shapely.is_empty(values))
    log["dropped_null_or_empty"] = int((~ok).sum())
    gdf = gdf[ok]

    lines = gdf.geom_type.isin(LINE_TYPES)
    log["dropped_non_line"] = int((~lines).sum())
    gdf = gdf[lines].copy()

    invalid = ~gdf.geometry.is_valid
    log["invalid_found"] = int(invalid.sum())
    repaired = unrepairable = 0
    if invalid.any():
        fixed = gdf.geometry[invalid].map(repair_line)
        good = fixed.notna()
        repaired, unrepairable = int(good.sum()), int((~good).sum())
        gdf.loc[fixed.index[good], gdf.geometry.name] = fixed[good]
        gdf = gdf.drop(index=fixed.index[~good])
    log["repaired_invalid"] = repaired
    log["dropped_unrepairable"] = unrepairable

    n = len(gdf)
    gdf = gdf.explode(index_parts=False)
    log["multilines_split_extra_parts"] = int(len(gdf) - n)

    parts = gdf.geometry.values
    usable = ~shapely.is_empty(parts) & shapely.is_valid(parts) & (shapely.length(parts) > 0)
    log["dropped_zero_length_parts"] = int((~usable).sum())
    gdf = gdf[usable]
    log["output_features"] = int(len(gdf))
    return gdf, log
