"""Validation of acquired datasets.

Checks: coordinate reference system, geographic coverage, geometry types, empty/null/invalid
geometries, duplicate records, missing attributes and completeness. Nothing is repaired or invented
here; the report says what is wrong. A report with any ``error`` issue means "do not proceed".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.config_defaults import NAIROBI_BBOX, STORAGE_SRID

if TYPE_CHECKING:  # heavy imports stay lazy
    import geopandas as gpd

LINE_TYPES = {"LineString", "MultiLineString"}
# Loose sanity limits. Nairobi County is about 696 km2 (area check on the boundary, see below).
COUNTY_AREA_KM2 = (550.0, 850.0)
BBOX_MARGIN_DEG = 0.03  # roads/waterways may run slightly past the county line
GRID_CELL_M = 1000.0


@dataclass
class Issue:
    level: str  # "error" blocks; "warning" needs a human look; "info" is context
    check: str
    message: str


@dataclass
class Report:
    dataset: str
    path: str
    issues: list[Issue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    checks_run: list[str] = field(default_factory=list)

    def add(self, level: str, check: str, message: str) -> None:
        self.issues.append(Issue(level, check, message))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {"dataset": self.dataset, "path": self.path, "ok": self.ok, "stats": self.stats,
                "checks_run": self.checks_run, "issues": [asdict(i) for i in self.issues]}


def _examples(values: Any, n: int = 5) -> str:
    return ", ".join(str(v) for v in list(values)[:n])


# ----------------------------------------------------------------------------- boundary
def validate_boundary(gdf: gpd.GeoDataFrame, path: str = "") -> Report:
    rep = Report("boundary", path)
    rep.checks_run += ["crs", "geometry", "area", "location"]
    if gdf.crs is None:
        rep.add("error", "crs", "boundary has no CRS")
        return rep
    if len(gdf) == 0 or gdf.geometry.isna().all() or gdf.geometry.is_empty.all():
        rep.add("error", "geometry", "boundary is empty")
        return rep
    if not set(gdf.geom_type) <= {"Polygon", "MultiPolygon"}:
        rep.add("error", "geometry", f"boundary must be polygonal, found {sorted(set(gdf.geom_type))}")
        return rep
    metric = gdf.to_crs(STORAGE_SRID)
    area = float(metric.geometry.union_all().area) / 1e6
    rep.stats["area_km2"] = round(area, 1)
    lo, hi = COUNTY_AREA_KM2
    if not lo <= area <= hi:
        rep.add("error", "area", f"boundary area {area:.0f} km2 is outside the expected {lo:.0f}-{hi:.0f} km2 "
                "for Nairobi County: wrong place or wrong admin level")
    minx, miny, maxx, maxy = gdf.to_crs(4326).total_bounds
    w, s, e, n = NAIROBI_BBOX
    if not (w - 0.1 <= minx and maxx <= e + 0.1 and s - 0.1 <= miny and maxy <= n + 0.1):
        rep.add("error", "location", f"boundary bounds {minx:.3f},{miny:.3f},{maxx:.3f},{maxy:.3f} "
                f"are not around Nairobi {NAIROBI_BBOX}")
    return rep


# ----------------------------------------------------------------------------- vectors
def validate_vector(
    gdf: gpd.GeoDataFrame,
    kind: str,
    *,
    path: str = "",
    expected_epsg: int,
    boundary: gpd.GeoDataFrame | None = None,
    min_features: int | None = None,
) -> Report:
    """Validate a roads or rivers layer. ``kind`` is ``"roads"`` or ``"rivers"``."""
    import numpy as np

    if kind not in {"roads", "rivers"}:
        raise ValueError(kind)
    rep = Report(kind, path)
    key_attr = "highway" if kind == "roads" else "waterway"
    floor = min_features if min_features is not None else (10_000 if kind == "roads" else 50)
    rep.stats["feature_count"] = int(len(gdf))

    # 1. CRS -----------------------------------------------------------------
    rep.checks_run.append("crs")
    if gdf.crs is None:
        rep.add("error", "crs", "layer has no CRS")
        return rep
    epsg = gdf.crs.to_epsg()
    rep.stats["crs"] = f"EPSG:{epsg}" if epsg else gdf.crs.name
    if epsg != expected_epsg:
        rep.add("error", "crs", f"CRS is {rep.stats['crs']}, expected EPSG:{expected_epsg}")

    if len(gdf) == 0:
        rep.add("error", "completeness", "layer has no features")
        return rep

    # 2. Empty / null / invalid / type --------------------------------------
    rep.checks_run += ["geometry_types", "empty_null_invalid"]
    geom = gdf.geometry
    null_mask = geom.isna()
    empty_mask = (~null_mask) & geom.is_empty
    live = geom[~null_mask & ~empty_mask]
    invalid_mask = ~live.is_valid
    types = gdf.geom_type.fillna("None").value_counts().to_dict()
    rep.stats["geometry_types"] = {str(k): int(v) for k, v in types.items()}
    bad_types = {t for t in types if t not in LINE_TYPES and t != "None"}
    if bad_types:
        rep.add("error", "geometry_types", f"non-line geometries present: {sorted(bad_types)}")
    for label, mask, src in (("null", null_mask, gdf), ("empty", empty_mask, gdf), ("invalid", invalid_mask, live)):
        count = int(mask.sum())
        rep.stats[f"{label}_geometries"] = count
        if count:
            rep.add("error", "empty_null_invalid", f"{count} {label} geometries, e.g. index {_examples(src.index[mask])}")

    # 3. Duplicates -----------------------------------------------------------
    rep.checks_run.append("duplicates")
    id_col = next((c for c in ("id", "osm_id") if c in gdf.columns), None)
    if id_col is not None:
        keys = gdf[[c for c in ("element", id_col) if c in gdf.columns]]
        dup_ids = int(keys.duplicated().sum())
        rep.stats["duplicate_ids"] = dup_ids
        if dup_ids:
            rep.add("error", "duplicates", f"{dup_ids} records repeat an OSM element id")
    else:
        rep.add("warning", "duplicates", "no OSM id column; id duplicates cannot be checked")
    if len(live):
        wkb = live.to_wkb()
        dup_geom = int(wkb.duplicated().sum())
        rep.stats["duplicate_geometries"] = dup_geom
        if dup_geom:
            rep.add("warning", "duplicates", f"{dup_geom} features have exactly the same geometry as another")

    # 4. Missing attributes ---------------------------------------------------
    rep.checks_run.append("attributes")
    if key_attr not in gdf.columns:
        rep.add("error", "attributes", f"required column '{key_attr}' is missing")
    else:
        nulls = int(gdf[key_attr].isna().sum())
        rep.stats[f"{key_attr}_null"] = nulls
        if nulls:
            rep.add("error", "attributes", f"{nulls} records have no '{key_attr}' value")
        rep.stats[f"{key_attr}_counts"] = {str(k): int(v) for k, v in gdf[key_attr].value_counts().head(15).items()}
    optional = ["name", "surface"] if kind == "roads" else ["name"]
    for col in optional:
        if col not in gdf.columns:
            rep.stats[f"{col}_missing_pct"] = 100.0
            rep.add("warning" if col == "surface" else "info", "attributes", f"column '{col}' absent")
            continue
        pct = round(float(gdf[col].isna().mean() * 100), 1)
        rep.stats[f"{col}_missing_pct"] = pct
        level = "warning" if col == "surface" and pct > 50 else "info"
        rep.add(level, "attributes", f"'{col}' missing on {pct}% of records (OSM tagging is incomplete; "
                "unknown surface falls back to a documented default in scoring)" if col == "surface"
                else f"'{col}' missing on {pct}% of records")

    # 5. Coverage / completeness ---------------------------------------------
    rep.checks_run += ["coverage", "completeness"]
    if len(gdf) < floor:
        rep.add("error", "completeness", f"only {len(gdf)} features; expected at least {floor} for Nairobi County")
    if len(live) and gdf.crs is not None:
        try:
            wgs = live.to_crs(4326)
        except Exception as exc:  # noqa: BLE001 - report, do not crash the validator
            rep.add("error", "coverage", f"cannot reproject to EPSG:4326: {exc}")
            return rep
        minx, miny, maxx, maxy = (float(v) for v in wgs.total_bounds)
        rep.stats["bounds_wgs84"] = [round(minx, 5), round(miny, 5), round(maxx, 5), round(maxy, 5)]
        if not np.isfinite([minx, miny, maxx, maxy]).all():
            rep.add("error", "coverage", "bounds are not finite")
            return rep
        w, s, e, n = NAIROBI_BBOX
        m = BBOX_MARGIN_DEG
        if minx < w - m or maxx > e + m or miny < s - m or maxy > n + m:
            rep.add("error", "coverage", f"extent {rep.stats['bounds_wgs84']} spills outside Nairobi {NAIROBI_BBOX}")
        if boundary is not None and boundary.crs is not None:
            _coverage_against_boundary(rep, live, boundary, kind)
        else:
            rep.add("warning", "coverage", "no county boundary supplied; only the rectangular bounding box was checked")
    return rep


def _coverage_against_boundary(rep: Report, live: gpd.GeoSeries, boundary: gpd.GeoDataFrame, kind: str) -> None:
    import geopandas as gpd
    import numpy as np
    from shapely import box

    bnd = boundary.to_crs(STORAGE_SRID)
    shape = bnd.geometry.union_all()
    metric = live.to_crs(STORAGE_SRID)
    inside = metric.intersects(shape.buffer(2000))
    outside = int((~inside).sum())
    rep.stats["features_outside_boundary_2km"] = outside
    if outside / max(len(metric), 1) > 0.01:
        rep.add("error", "coverage", f"{outside} features lie more than 2 km outside the county boundary")
    elif outside:
        rep.add("warning", "coverage", f"{outside} features lie more than 2 km outside the county boundary")

    if kind == "roads":
        minx, miny, maxx, maxy = shape.bounds
        xs = np.arange(minx, maxx, GRID_CELL_M)
        ys = np.arange(miny, maxy, GRID_CELL_M)
        cells = gpd.GeoDataFrame(geometry=[box(x, y, x + GRID_CELL_M, y + GRID_CELL_M) for x in xs for y in ys],
                                 crs=STORAGE_SRID)
        cells = cells[cells.geometry.centroid.within(shape)].reset_index(drop=True)
        hit = gpd.sjoin(cells, gpd.GeoDataFrame(geometry=metric.values, crs=STORAGE_SRID), predicate="intersects", how="inner").index.nunique()
        share = hit / max(len(cells), 1)
        rep.stats["grid_cells_1km"] = int(len(cells))
        rep.stats["grid_cells_with_road_pct"] = round(share * 100, 1)
        if share < 0.5:
            rep.add("error", "completeness", f"only {share:.0%} of 1 km cells in the county contain a road: "
                    "the download looks truncated")
        elif share < 0.7:
            rep.add("warning", "completeness", f"{share:.0%} of 1 km cells contain a road "
                    "(parks and forest legitimately have none)")


# ----------------------------------------------------------------------------- raster
def validate_dem(path: Path, boundary: gpd.GeoDataFrame | None = None) -> Report:
    import numpy as np
    import rasterio

    rep = Report("dem", str(path))
    rep.checks_run += ["readable", "crs", "resolution", "nodata", "coverage", "value_range"]
    try:
        src = rasterio.open(path)
    except Exception as exc:  # noqa: BLE001
        rep.add("error", "readable", f"cannot open raster: {exc}")
        return rep
    with src:
        rep.stats.update(width=src.width, height=src.height, bands=src.count, dtype=src.dtypes[0],
                         nodata=src.nodata, bounds=[round(v, 6) for v in src.bounds])
        if src.count != 1:
            rep.add("error", "readable", f"expected 1 band, found {src.count}")
        if src.crs is None:
            rep.add("error", "crs", "raster has no CRS")
            return rep
        rep.stats["crs"] = f"EPSG:{src.crs.to_epsg()}" if src.crs.to_epsg() else src.crs.to_string()
        xres, yres = src.res
        rep.stats["resolution"] = [xres, yres]
        if src.crs.is_geographic:
            metres = xres * 111_320.0
            if src.crs.to_epsg() != 4326:
                rep.add("warning", "crs", f"geographic CRS is {rep.stats['crs']}, not EPSG:4326")
        else:
            metres = xres
        rep.stats["approx_cell_m"] = round(metres, 1)
        if not 20 <= metres <= 40:
            rep.add("error", "resolution", f"cell size about {metres:.0f} m; a 30 m DEM was required")
        if src.nodata is None:
            rep.add("warning", "nodata", "no nodata value declared; voids cannot be told from elevations")
        data = src.read(1, masked=False).astype("float64")
        mask = ~np.isfinite(data)
        if src.nodata is not None:
            mask |= data == src.nodata
        void_pct = float(mask.mean() * 100)
        rep.stats["void_pct"] = round(void_pct, 3)
        if void_pct > 5:
            rep.add("error", "nodata", f"{void_pct:.1f}% of cells are void")
        elif void_pct > 0.5:
            rep.add("warning", "nodata", f"{void_pct:.2f}% of cells are void; they stay NODATA, not filled")
        valid = data[~mask]
        if valid.size == 0:
            rep.add("error", "value_range", "no valid elevation values")
            return rep
        lo, hi = float(valid.min()), float(valid.max())
        rep.stats.update(min_m=lo, max_m=hi, mean_m=round(float(valid.mean()), 1))
        if lo < -100 or hi > 6000:
            rep.add("error", "value_range", f"elevation {lo:.0f}..{hi:.0f} m is not plausible for Nairobi")
        elif lo < 1300 or hi > 2700:
            rep.add("warning", "value_range", f"elevation {lo:.0f}..{hi:.0f} m is outside the usual Nairobi range (~1,400-2,500 m)")
        if lo == hi:
            rep.add("error", "value_range", "all valid cells have the same value")
        _dem_coverage(rep, src, mask, boundary)
    return rep


def _dem_coverage(rep: Report, src: Any, void_mask: Any, boundary: gpd.GeoDataFrame | None) -> None:
    import numpy as np
    from rasterio.features import geometry_mask

    if boundary is None or boundary.crs is None:
        w, s, e, n = NAIROBI_BBOX
        need = (w, s, e, n)
        rep.add("warning", "coverage", "no county boundary supplied; checked against the rectangular bounding box only")
        bounds = tuple(src.bounds) if src.crs.to_epsg() == 4326 else None
        if bounds and not (bounds[0] <= w and bounds[1] <= s and bounds[2] >= e and bounds[3] >= n):
            rep.add("error", "coverage", f"raster bounds {rep.stats['bounds']} do not cover {need}")
        return
    shape = boundary.to_crs(src.crs).geometry
    minx, miny, maxx, maxy = shape.total_bounds
    b = src.bounds
    if not (b.left <= minx and b.bottom <= miny and b.right >= maxx and b.top >= maxy):
        rep.add("error", "coverage", "raster extent does not fully cover the county boundary")
    inside = ~geometry_mask(list(shape), out_shape=(src.height, src.width), transform=src.transform)
    n_in = int(inside.sum())
    rep.stats["cells_inside_county"] = n_in
    if n_in == 0:
        rep.add("error", "coverage", "no raster cells fall inside the county boundary")
        return
    voids = float(np.logical_and(inside, void_mask).sum() / n_in * 100)
    rep.stats["void_inside_county_pct"] = round(voids, 3)
    if voids > 1:
        rep.add("error", "coverage", f"{voids:.1f}% of cells inside the county are void")
