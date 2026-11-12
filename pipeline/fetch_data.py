"""Step 1: acquire source data, save it unmodified under ``data/raw/`` and record provenance.

Sources
- County boundary: OpenStreetMap relation via Nominatim (OSMnx), or a boundary file you supply.
- Roads, waterways: OpenStreetMap via the Overpass API (OSMnx ``features_from_polygon``).
- Elevation: SRTM 1 arc-second (about 30 m) 1x1 degree tiles in the "skadi" layout from the
  public AWS Open Data bucket ``elevation-tiles-prod`` (Mapzen/Tilezen terrain tiles).

Nothing is generated or filled in: if a source cannot be reached the function raises
:class:`~pipeline.errors.SourceUnavailable` and the caller reports it.
Heavy libraries are imported inside functions.
"""

from __future__ import annotations

import gzip
import math
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.config_defaults import NAIROBI_BBOX, STORAGE_SRID
from pipeline import validate
from pipeline.errors import AcquisitionError, SourceUnavailable, ValidationFailed
from pipeline.provenance import (
    OSM_ATTRIBUTION,
    OSM_LICENCE,
    SRTM_ATTRIBUTION,
    SRTM_LICENCE,
    Provenance,
    write_provenance,
)

if TYPE_CHECKING:
    import geopandas as gpd

PLACE_QUERY = "Nairobi County, Kenya"
OVERPASS_NOTE = "Overpass API (endpoint from OSMnx settings)"
ROAD_TAGS: dict[str, Any] = {"highway": True}
RIVER_TAGS: dict[str, Any] = {"waterway": ["river", "stream", "canal", "drain"]}
DEM_BUCKET_URL = "https://elevation-tiles-prod.s3.amazonaws.com/skadi"
DEM_BUFFER_DEG = 0.02  # clip margin so edge cells of the county are covered

RAW_BOUNDARY = "boundary/nairobi_county_boundary.gpkg"
RAW_ROADS = "osm/nairobi_roads.gpkg"
RAW_RIVERS = "rivers/nairobi_rivers.gpkg"
RAW_DEM_DIR = "dem"
DEM_MOSAIC = "dem/nairobi_srtm_30m.tif"

OSM_LIMITATIONS = [
    "OpenStreetMap is volunteer-mapped: completeness and tagging vary by area and change over time.",
    "The 'surface' tag is present on only a minority of roads; missing values are not guessed.",
    "The extract is a snapshot at the retrieval time recorded here; re-running gives newer data.",
]
DEM_LIMITATIONS = [
    "SRTM was acquired in February 2000: it predates later construction and land-use change.",
    "It is a surface model (canopy and buildings included), not a bare-earth terrain model, with a "
    "vertical error of several metres; small drainage features are not resolved at 30 m.",
    "Voids are flagged -32768 and are left as NODATA, never filled.",
    "Elevations refer to the EGM96 geoid.",
]


# --------------------------------------------------------------------------- helpers
def _retry(action: str, func: Any, attempts: int = 3, delay: float = 5.0) -> Any:
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - re-raised below with context
            last = exc
            if i < attempts:
                time.sleep(delay * i)
    raise SourceUnavailable(
        f"{action} failed after {attempts} attempts: {type(last).__name__}: {last}. "
        "Check the internet connection and that the host is reachable from this machine "
        "(corporate/cloud networks sometimes block it), then run the command again."
    ) from last


def _flatten_for_gpkg(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """GeoPackage columns must be scalars; stringify lists/dicts but leave values otherwise as-is."""
    gdf = gdf.copy()
    # GeoPackage (SQLite) column names are case-insensitive, OSM tag keys are not ('FIXME' and 'fixme'
    # both exist). Keep both: the later one gets a numeric suffix instead of being dropped or merged.
    seen: dict[str, int] = {}
    renames: dict[str, str] = {}
    for col in gdf.columns:
        key = str(col).lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            renames[col] = f"{col}__{seen[key]}"
    if renames:
        gdf = gdf.rename(columns=renames)
    for col in gdf.columns:
        if col == gdf.geometry.name:
            continue
        if gdf[col].map(lambda v: isinstance(v, (list, tuple, dict, set))).any():
            gdf[col] = gdf[col].map(lambda v: None if v is None else (str(v) if isinstance(v, (list, tuple, dict, set)) else v))
    return gdf


def _configure_osmnx(cache_dir: Path) -> Any:
    import osmnx as ox

    cache_dir.mkdir(parents=True, exist_ok=True)
    ox.settings.cache_folder = str(cache_dir / "osmnx")
    ox.settings.use_cache = True
    ox.settings.log_console = False
    ox.settings.requests_timeout = 300
    return ox


def _skip(path: Path, force: bool) -> bool:
    return path.exists() and not force


# --------------------------------------------------------------------------- boundary
def fetch_boundary(raw_dir: Path, cache_dir: Path, *, boundary_file: Path | None = None, force: bool = False) -> Path:
    """Obtain the Nairobi County polygon, check it, save it raw (EPSG:4326)."""
    import geopandas as gpd

    out = raw_dir / RAW_BOUNDARY
    if _skip(out, force):
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    if boundary_file is not None:
        if not boundary_file.exists():
            raise AcquisitionError(f"boundary file not found: {boundary_file}")
        gdf = gpd.read_file(boundary_file)
        source, url, method = f"User-supplied file {boundary_file.name}", "", "local file"
        licence, attribution = "As supplied by the user (not verified)", "As supplied by the user"
    else:
        ox = _configure_osmnx(cache_dir)
        gdf = _retry("Nominatim boundary lookup", lambda: ox.geocode_to_gdf(PLACE_QUERY))
        source, url, method = "OpenStreetMap via Nominatim", "https://nominatim.openstreetmap.org", "osmnx.geocode_to_gdf"
        licence, attribution = OSM_LICENCE, OSM_ATTRIBUTION
    if gdf.crs is None:
        raise AcquisitionError("boundary has no CRS; supply a file with a defined CRS")
    gdf = gdf.to_crs(4326)
    gdf = gdf[gdf.geom_type.isin(["Polygon", "MultiPolygon"])].head(1).copy()
    report = validate.validate_boundary(gdf)
    if not report.ok:
        raise ValidationFailed("boundary rejected: " + "; ".join(i.message for i in report.errors))
    keep = [c for c in ("display_name", "name", "osm_id", "osm_type", "geometry") if c in gdf.columns]
    _flatten_for_gpkg(gdf[keep]).to_file(out, driver="GPKG")
    rec = Provenance("boundary", source, url, licence, attribution, method,
                     parameters={"query": PLACE_QUERY if boundary_file is None else str(boundary_file)},
                     limitations=["Administrative boundary as mapped in OSM; it may differ slightly from the official survey line."]
                     if boundary_file is None else [],
                     feature_count=len(gdf), crs="EPSG:4326")
    rec.add_file(out, raw_dir)
    write_provenance(rec, out)
    return out


# --------------------------------------------------------------------------- OSM vectors
def _fetch_osm(kind: str, tags: dict[str, Any], rel_out: str, raw_dir: Path, cache_dir: Path,
               boundary_path: Path, force: bool, limitations: list[str]) -> Path:
    import geopandas as gpd

    out = raw_dir / rel_out
    if _skip(out, force):
        return out
    if not boundary_path.exists():
        raise AcquisitionError(f"county boundary missing ({boundary_path}); run the 'boundary' step first")
    polygon = gpd.read_file(boundary_path).to_crs(4326).union_all()
    ox = _configure_osmnx(cache_dir)
    gdf = _retry(f"Overpass {kind} download", lambda: ox.features_from_polygon(polygon, tags))
    gdf = gdf.reset_index()  # element, id become columns
    lines = gdf[gdf.geom_type.isin(["LineString", "MultiLineString"])].copy()
    dropped = {str(k): int(v) for k, v in gdf[~gdf.index.isin(lines.index)].geom_type.value_counts().items()}
    if lines.empty:
        raise AcquisitionError(f"Overpass returned no line features for {kind}; refusing to write an empty dataset")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.gpkg")
    _flatten_for_gpkg(lines).to_file(tmp, driver="GPKG")  # all tag columns kept: this is the raw copy
    tmp.replace(out)
    rec = Provenance(kind, "OpenStreetMap via the Overpass API (OSMnx features_from_polygon)",
                     "https://overpass-api.de", OSM_LICENCE, OSM_ATTRIBUTION, "osmnx.features_from_polygon",
                     parameters={"tags": tags, "area": PLACE_QUERY, "boundary_file": boundary_path.name,
                                 "non_line_features_not_saved": dropped},
                     limitations=limitations, feature_count=len(lines), crs="EPSG:4326")
    rec.add_file(out, raw_dir)
    write_provenance(rec, out)
    return out


def fetch_roads(raw_dir: Path, cache_dir: Path, *, force: bool = False) -> Path:
    """Every OSM ``highway`` line inside the county, unfiltered. Class filtering happens later (A3)."""
    return _fetch_osm("roads", ROAD_TAGS, RAW_ROADS, raw_dir, cache_dir, raw_dir / RAW_BOUNDARY, force, OSM_LIMITATIONS)


def fetch_rivers(raw_dir: Path, cache_dir: Path, *, force: bool = False) -> Path:
    """OSM ``waterway`` lines (river, stream, canal, drain) as river/drainage centrelines."""
    lim = OSM_LIMITATIONS + [
        "Waterway lines are mapped centrelines, not flood extents; seasonal and piped channels may be missing or "
        "mapped as 'drain'.",
    ]
    return _fetch_osm("rivers", RIVER_TAGS, RAW_RIVERS, raw_dir, cache_dir, raw_dir / RAW_BOUNDARY, force, lim)


# --------------------------------------------------------------------------- DEM
def tile_names(west: float, south: float, east: float, north: float) -> list[str]:
    """Skadi tile names (``S02E036``) covering a lon/lat box. Tiles are named by their SW corner."""
    names = []
    for lat in range(math.floor(south), math.ceil(north)):
        for lon in range(math.floor(west), math.ceil(east)):
            ns = "N" if lat >= 0 else "S"
            ew = "E" if lon >= 0 else "W"
            names.append(f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}")
    return names


def tile_url(name: str, base: str = DEM_BUCKET_URL) -> str:
    return f"{base}/{name[:3]}/{name}.hgt.gz"


def _download(url: str, dest: Path, session: Any) -> None:
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    def go() -> None:
        with session.get(url, stream=True, timeout=(10, 120)) as resp:
            if resp.status_code == 404:
                raise AcquisitionError(f"tile not found (HTTP 404): {url}")
            resp.raise_for_status()
            expected = resp.headers.get("Content-Length")
            with part.open("wb") as fh:
                for block in resp.iter_content(1 << 20):
                    fh.write(block)
            if expected is not None and part.stat().st_size != int(expected):
                raise requests.ConnectionError(f"truncated download {part.stat().st_size}/{expected} bytes")

    try:
        _retry(f"download {url}", go)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    part.replace(dest)


def fetch_dem(raw_dir: Path, *, boundary_path: Path | None = None, dem_file: Path | None = None,
              dem_source: str | None = None, force: bool = False, session: Any = None) -> Path:
    """Obtain SRTM 30 m elevation and write a single clipped GeoTIFF (EPSG:4326) covering the county.

    ``dem_file`` adopts a GeoTIFF you downloaded yourself (for example from USGS EarthExplorer); you
    must describe it with ``dem_source`` so provenance is not left blank.
    """
    import geopandas as gpd
    import rasterio
    import requests
    from rasterio.merge import merge
    from rasterio.windows import from_bounds

    out = raw_dir / DEM_MOSAIC
    if _skip(out, force):
        return out
    out.parent.mkdir(parents=True, exist_ok=True)

    if dem_file is not None:
        if not dem_source:
            raise AcquisitionError("--dem-file needs --dem-source describing where the file came from and its licence")
        if not dem_file.exists():
            raise AcquisitionError(f"DEM file not found: {dem_file}")
        shutil.copyfile(dem_file, out)
        rec = Provenance("dem", dem_source, "", "As stated by the user (not verified)", dem_source, "user-supplied file",
                         parameters={"original_name": dem_file.name}, limitations=DEM_LIMITATIONS)
        _finish_dem(rec, out, raw_dir, boundary_path)
        return out

    if boundary_path is not None and boundary_path.exists():
        w, s, e, n = gpd.read_file(boundary_path).to_crs(4326).total_bounds
    else:
        w, s, e, n = NAIROBI_BBOX
    w, s, e, n = w - DEM_BUFFER_DEG, s - DEM_BUFFER_DEG, e + DEM_BUFFER_DEG, n + DEM_BUFFER_DEG
    names = tile_names(w, s, e, n)
    session = session or requests.Session()
    tiles_dir = raw_dir / RAW_DEM_DIR / "tiles"
    hgt_paths: list[Path] = []
    for name in names:
        gz = tiles_dir / f"{name}.hgt.gz"
        if not gz.exists() or force:
            _download(tile_url(name), gz, session)
        hgt = tiles_dir / f"{name}.hgt"
        try:
            with gzip.open(gz, "rb") as src, hgt.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        except (OSError, EOFError) as exc:
            gz.unlink(missing_ok=True)
            raise AcquisitionError(f"{gz.name} is not a valid gzip file ({exc}); deleted, run again") from exc
        if hgt.stat().st_size not in (3601 * 3601 * 2, 1201 * 1201 * 2):
            raise AcquisitionError(f"{hgt.name} has {hgt.stat().st_size} bytes: not a 1- or 3-arc-second SRTM tile")
        hgt_paths.append(hgt)

    datasets = [rasterio.open(p) for p in hgt_paths]
    try:
        mosaic, transform = merge(datasets)
        nodata = datasets[0].nodata if datasets[0].nodata is not None else -32768
        profile = {"driver": "GTiff", "height": mosaic.shape[1], "width": mosaic.shape[2], "count": 1,
                   "dtype": mosaic.dtype, "crs": "EPSG:4326", "transform": transform, "nodata": nodata,
                   "compress": "deflate", "predictor": 2, "tiled": True}
        tmp = out.with_name("_mosaic.tif")
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(mosaic)
    finally:
        for ds in datasets:
            ds.close()
    with rasterio.open(tmp) as full:
        win = from_bounds(w, s, e, n, transform=full.transform).round_offsets().round_lengths()
        data = full.read(1, window=win)
        prof = full.profile | {"height": data.shape[0], "width": data.shape[1],
                               "transform": full.window_transform(win)}
        with rasterio.open(out, "w", **prof) as dst:
            dst.write(data, 1)
    tmp.unlink()
    rec = Provenance(
        "dem", "SRTM 1 arc-second (SRTMGL1) via Mapzen/Tilezen terrain tiles, 'skadi' layout",
        f"{DEM_BUCKET_URL}/ (AWS Open Data bucket elevation-tiles-prod)", SRTM_LICENCE, SRTM_ATTRIBUTION,
        "HTTP download of .hgt.gz tiles, merged and clipped with rasterio",
        parameters={"tiles": names, "clip_bounds_wgs84": [w, s, e, n], "buffer_deg": DEM_BUFFER_DEG},
        limitations=DEM_LIMITATIONS, crs="EPSG:4326")
    for p in hgt_paths:
        p.unlink()  # keep only the original .gz tiles; the .hgt copies are re-creatable
    for gz in sorted(tiles_dir.glob("*.hgt.gz")):
        rec.add_file(gz, raw_dir)
    _finish_dem(rec, out, raw_dir, boundary_path)
    return out


def _finish_dem(rec: Provenance, out: Path, raw_dir: Path, boundary_path: Path | None = None) -> None:
    rec.add_file(out, raw_dir)
    write_provenance(rec, out)
    import geopandas as gpd

    boundary = gpd.read_file(boundary_path) if boundary_path is not None and boundary_path.exists() else None
    report = validate.validate_dem(out, boundary)
    if report.errors:
        raise ValidationFailed("DEM written but failed checks: " + "; ".join(i.message for i in report.errors))
