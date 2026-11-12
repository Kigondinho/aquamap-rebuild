"""Step 2: DEM preprocessing and terrain analysis (Rasterio + NumPy).

    python -m pipeline.prepare_dem            # raw DEM -> data/processed/dem_32737.tif + slope_pct_32737.tif
    python -m pipeline.prepare_dem --force    # regenerate

Method
1. Read the raw DEM; the declared nodata (SRTM void -32768) and any non-finite cell are masked.
2. Reproject to EPSG:32737 on a regular metric grid (default 30 m) whose origin is snapped to a multiple
   of the cell size, so the result is the same every run. Resampling is **bilinear**: elevation is
   continuous, and GDAL leaves nodata cells out of the kernel, so voids are not smeared into data.
   Output float32, nodata -9999, with CRS, transform and tags preserved or written.
3. Slope uses Horn's 3x3 method (the one used by GDAL/ArcGIS) with the true cell size in metres:
   slope % = 100 * sqrt(dz/dx^2 + dz/dy^2) = 100 * tan(angle). A cell gets a slope only if all nine cells
   of its 3x3 window are valid, so nodata borders and raster edges never produce fake steep values;
   those cells are nodata in the slope raster. Slope is written on exactly the same grid as elevation.
UTM scale error at Nairobi is about 0.03 %, negligible for slope.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.config_defaults import STORAGE_SRID
from pipeline.errors import AcquisitionError
from pipeline.provenance import SRTM_ATTRIBUTION, SRTM_LICENCE, Provenance, read_provenance, utc_now_iso, write_provenance

if TYPE_CHECKING:
    import numpy as np

RAW_DEM = "raw/dem/nairobi_srtm_30m.tif"
OUT_DEM = "processed/dem_32737.tif"
OUT_SLOPE = "processed/slope_pct_32737.tif"
OUT_REPORT = "processed/dem_validation_report.json"
OUT_NODATA = -9999.0
DEFAULT_RES = 30.0
RESAMPLING = "bilinear"

LIMITATIONS = [
    "Slope is a 30 m neighbourhood estimate from SRTM; it smooths small embankments and cuttings and "
    "includes SRTM vertical noise.",
    "Cells within one cell of nodata or of the raster edge have no slope (left as nodata, not extrapolated).",
]


# ---------------------------------------------------------------------------- pure array maths
def horn_slope_percent(elev: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Slope in percent from a 2-D elevation array (NaN = nodata). Edge/void-adjacent cells are NaN.

    ``dx``/``dy`` are positive cell sizes in the same unit as elevation (metres).
    """
    import numpy as np

    z = np.asarray(elev, dtype="float64")
    out = np.full(z.shape, np.nan)
    if z.ndim != 2 or z.shape[0] < 3 or z.shape[1] < 3:
        return out
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * dx)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * dy)  # rows increase southwards
    out[1:-1, 1:-1] = 100.0 * np.hypot(dzdx, dzdy)  # NaN in any window cell propagates
    out[~np.isfinite(z)] = np.nan  # Horn ignores the centre cell, so mask nodata cells explicitly
    return out


# ---------------------------------------------------------------------------- steps
def reproject_dem(src_path: Path, dst_path: Path, *, resolution: float = DEFAULT_RES, dst_epsg: int = STORAGE_SRID) -> Path:
    """Reproject to the metric CRS with bilinear resampling; voids become nodata (-9999)."""
    import numpy as np
    import rasterio
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.warp import reproject, transform_bounds

    if not src_path.exists():
        raise AcquisitionError(f"raw DEM not found: {src_path}; run 'python -m pipeline.acquire fetch dem'")
    dst_crs = CRS.from_epsg(dst_epsg)
    with rasterio.open(src_path) as src:
        if src.crs is None:
            raise AcquisitionError(f"{src_path.name} has no CRS; cannot reproject safely")
        if src.count != 1:
            raise AcquisitionError(f"{src_path.name} has {src.count} bands; expected 1")
        # extent of the reprojected raster, then snap outward to the cell grid
        left, bottom, right, top = transform_bounds(src.crs, dst_crs, *src.bounds, densify_pts=21)
        left, bottom = np.floor(left / resolution) * resolution, np.floor(bottom / resolution) * resolution
        right, top = np.ceil(right / resolution) * resolution, np.ceil(top / resolution) * resolution
        width, height = int(round((right - left) / resolution)), int(round((top - bottom) / resolution))
        transform = from_origin(left, top, resolution, resolution)
        data = np.full((height, width), OUT_NODATA, dtype="float32")
        src_nodata = src.nodata
        if src_nodata is None and src.dtypes[0] == "int16":
            src_nodata = -32768  # SRTM void marker; some tools drop the nodata tag but keep the value
        reproject(source=rasterio.band(src, 1), destination=data, src_transform=src.transform, src_crs=src.crs,
                  src_nodata=src_nodata, dst_transform=transform, dst_crs=dst_crs, dst_nodata=OUT_NODATA,
                  resampling=Resampling.bilinear)
        data[~np.isfinite(data)] = OUT_NODATA
        tags = {k: v for k, v in src.tags().items()}
        tags.update(RESAMPLING=RESAMPLING, SOURCE_FILE=src_path.name, UNITS="metres", VERTICAL_DATUM="EGM96 (as SRTM)")
        profile = {"driver": "GTiff", "height": height, "width": width, "count": 1, "dtype": "float32",
                   "crs": dst_crs, "transform": transform, "nodata": OUT_NODATA, "compress": "deflate",
                   "predictor": 3, "tiled": True, "blockxsize": 256, "blockysize": 256}
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst_path.with_name(dst_path.stem + ".part.tif")
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(data, 1)
            dst.update_tags(**tags)
            dst.set_band_description(1, "elevation (m)")
    tmp.replace(dst_path)
    return dst_path


def compute_slope(dem_path: Path, dst_path: Path) -> Path:
    """Write slope in percent on exactly the grid of ``dem_path`` (nodata -9999)."""
    import numpy as np
    import rasterio

    with rasterio.open(dem_path) as src:
        if src.crs is None or src.crs.is_geographic:
            raise AcquisitionError("slope needs a projected (metre) DEM; run reproject_dem first")
        z = src.read(1).astype("float64")
        mask = ~np.isfinite(z)
        if src.nodata is not None:
            mask |= z == src.nodata
        z[mask] = np.nan
        slope = horn_slope_percent(z, abs(src.transform.a), abs(src.transform.e))
        out = np.where(np.isfinite(slope), slope, OUT_NODATA).astype("float32")
        profile = src.profile | {"dtype": "float32", "nodata": OUT_NODATA, "compress": "deflate", "predictor": 3}
        tags = src.tags() | {"UNITS": "percent (100*tan(angle))", "METHOD": "Horn 3x3, nodata-aware"}
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst_path.with_name(dst_path.stem + ".part.tif")
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(out, 1)
            dst.update_tags(**tags)
            dst.set_band_description(1, "slope (%)")
    tmp.replace(dst_path)
    return dst_path


# ---------------------------------------------------------------------------- validation
def validate_terrain(dem_path: Path, slope_path: Path, *, resolution: float = DEFAULT_RES,
                     max_slope_warn: float = 150.0) -> dict[str, Any]:
    """Check both rasters. Returns {'ok', 'errors', 'warnings', 'stats'}."""
    import numpy as np
    import rasterio

    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {}
    with rasterio.open(dem_path) as d, rasterio.open(slope_path) as s:
        stats.update(width=d.width, height=d.height, bounds=[round(v, 3) for v in d.bounds],
                     crs=f"EPSG:{d.crs.to_epsg()}" if d.crs else None, cell=[d.res[0], d.res[1]])
        if d.crs is None or d.crs.to_epsg() != STORAGE_SRID:
            errors.append(f"DEM CRS is {stats['crs']}, expected EPSG:{STORAGE_SRID}")
        if s.crs != d.crs:
            errors.append("slope CRS differs from DEM CRS")
        if (d.width, d.height) != (s.width, s.height):
            errors.append(f"dimensions differ: DEM {d.width}x{d.height}, slope {s.width}x{s.height}")
        if d.transform != s.transform or tuple(d.bounds) != tuple(s.bounds):
            errors.append("slope is not aligned with the DEM (transform/bounds differ)")
        if abs(d.res[0] - resolution) > 1e-6 or abs(d.res[1] - resolution) > 1e-6:
            errors.append(f"cell size {d.res}, expected {resolution} m")
        if (d.transform.c / resolution) % 1 > 1e-9 and (1 - (d.transform.c / resolution) % 1) > 1e-9:
            warnings.append("grid origin is not a multiple of the cell size")
        for name, r in (("DEM", d), ("slope", s)):
            if r.nodata is None:
                errors.append(f"{name} has no nodata value declared")
            if r.dtypes[0] != "float32":
                warnings.append(f"{name} dtype is {r.dtypes[0]}")
        ez = d.read(1).astype("float64")
        sz = s.read(1).astype("float64")
        em = ~np.isfinite(ez) | (ez == d.nodata)
        sm = ~np.isfinite(sz) | (sz == s.nodata)
        if ez.shape != sz.shape:
            return {"ok": False, "errors": errors, "warnings": warnings, "stats": stats}
        stats["elevation_valid_pct"] = round(float((~em).mean() * 100), 2)
        if em.all():
            errors.append("DEM has no valid cells")
            return {"ok": False, "errors": errors, "warnings": warnings, "stats": stats}
        ev = ez[~em]
        stats.update(elevation_min=float(ev.min()), elevation_max=float(ev.max()), elevation_mean=round(float(ev.mean()), 2))
        if ev.min() < -100 or ev.max() > 6000:
            errors.append(f"elevation {ev.min():.0f}..{ev.max():.0f} m is not plausible")
        elif ev.min() < 1300 or ev.max() > 2700:
            warnings.append(f"elevation {ev.min():.0f}..{ev.max():.0f} m is outside the usual Nairobi range")
        if np.any(ez[em & np.isfinite(ez)] != d.nodata):
            errors.append("masked DEM cells hold values other than nodata")

        # slope must exist only where all 3x3 neighbours are valid elevation
        pad = np.pad(~em, 1, constant_values=False)
        full_window = np.ones_like(em, dtype=bool)
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                full_window &= pad[dy:dy + em.shape[0], dx:dx + em.shape[1]]
        stats["slope_valid_pct"] = round(float((~sm).mean() * 100), 2)
        wrongly_set = int((~sm & ~full_window).sum())
        wrongly_missing = int((sm & full_window).sum())
        stats["slope_cells_next_to_nodata"] = wrongly_set
        if wrongly_set:
            errors.append(f"{wrongly_set} slope cells touch nodata/edge (would be unreliable)")
        if wrongly_missing:
            errors.append(f"{wrongly_missing} cells with a full valid 3x3 window have no slope")
        sv = sz[~sm]
        if sv.size == 0:
            errors.append("slope raster has no valid cells")
        else:
            q = np.percentile(sv, [50, 95, 99])
            stats.update(slope_min=round(float(sv.min()), 3), slope_max=round(float(sv.max()), 2),
                         slope_mean=round(float(sv.mean()), 2), slope_median=round(float(q[0]), 2),
                         slope_p95=round(float(q[1]), 2), slope_p99=round(float(q[2]), 2))
            if sv.min() < 0:
                errors.append("negative slope values")
            if not np.isfinite(sv).all():
                errors.append("non-finite slope values")
            if sv.max() > max_slope_warn:
                warnings.append(f"maximum slope {sv.max():.0f}% is very steep (check for DEM artefacts)")
            if q[2] > 60:
                warnings.append(f"99th percentile slope {q[2]:.0f}% is high for Nairobi")
            if sv.max() <= 1.0 and sv.size > 100:
                errors.append("slope values all <= 1: looks like radians/degrees/ratio, not percent")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


# ---------------------------------------------------------------------------- orchestration
def run(data_dir: Path, *, raw_dem: Path | None = None, resolution: float = DEFAULT_RES, force: bool = False) -> dict[str, Any]:
    raw = raw_dem or data_dir / RAW_DEM
    dem_out, slope_out = data_dir / OUT_DEM, data_dir / OUT_SLOPE
    if force or not (dem_out.exists() and slope_out.exists()):
        reproject_dem(raw, dem_out, resolution=resolution)
        compute_slope(dem_out, slope_out)
        raw_rec = read_provenance(raw) or {}
        for path, name, note in ((dem_out, "dem_32737", "elevation (m), reprojected"), (slope_out, "slope_pct", "slope (%), Horn 3x3")):
            rec = Provenance(name, raw_rec.get("source_name", "unknown (raw DEM has no provenance record)"),
                             raw_rec.get("source_url", ""), raw_rec.get("licence", SRTM_LICENCE),
                             raw_rec.get("attribution", SRTM_ATTRIBUTION), "pipeline.prepare_dem",
                             parameters={"resampling": RESAMPLING, "resolution_m": resolution, "nodata": OUT_NODATA,
                                         "output": note, "slope_method": "Horn 3x3, nodata-aware, percent"},
                             limitations=raw_rec.get("limitations", []) + LIMITATIONS, crs=f"EPSG:{STORAGE_SRID}",
                             stage="processed", derived_from=[raw.name])
            rec.add_file(path, data_dir)
            write_provenance(rec, path)
    result = validate_terrain(dem_out, slope_out, resolution=resolution)
    report = data_dir / OUT_REPORT
    report.write_text(json.dumps({"generated_at": utc_now_iso(), "dem": OUT_DEM, "slope": OUT_SLOPE, **result},
                                 indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.prepare_dem", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    p.add_argument("--raw-dem", type=Path, help=f"default: <data-dir>/{RAW_DEM}")
    p.add_argument("--resolution", type=float, default=DEFAULT_RES, help="output cell size in metres")
    p.add_argument("--force", action="store_true", help="regenerate even if outputs exist")
    args = p.parse_args(argv)
    try:
        result = run(args.data_dir, raw_dem=args.raw_dem, resolution=args.resolution, force=args.force)
    except AcquisitionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print("PASS" if result["ok"] else "FAIL")
    for k, v in result["stats"].items():
        print(f"    {k}: {v}")
    for w in result["warnings"]:
        print(f"    WARNING {w}")
    for e in result["errors"]:
        print(f"    ERROR {e}", file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
