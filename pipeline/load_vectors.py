"""Steps 3-4: load processed road and river lines into PostGIS (EPSG:32737) and verify the result.

    python -m pipeline.load_vectors                  # load roads + rivers, then verify against the files
    python -m pipeline.load_vectors --only rivers
    python -m pipeline.load_vectors --verify-only    # compare database and files, change nothing
    python -m pipeline.load_vectors --prune          # also delete rows that are no longer in the file

What happens to each layer, in ONE transaction (any error rolls everything back):
1. Read ``data/processed/<layer>.gpkg``; refuse a file without a CRS; reproject to EPSG:32737 if needed.
2. Clean: drop exact duplicate records, null/empty geometries and non-lines; repair invalid lines
   (unrepairable ones are dropped); split MultiLineStrings; drop zero-length parts; drop rows without
   an OSM id or without a required attribute. Every drop is counted in the report.
3. Copy the rows into a temporary staging table and let PostGIS re-check validity, SRID and type.
4. Upsert into the target table on the key ``(osm_element, osm_id, part_no)``. A re-run with the same
   file inserts nothing and updates nothing; a changed feature is updated in place (its ``road_id``
   is kept, and a road whose geometry changed loses its now-stale vulnerability result).
5. Record the layer in ``spatial_layers``, make sure the GiST index exists, ``ANALYZE``.

All road classes are loaded (decision A3 is applied when scoring, by ``highway_type``).
Exit status 0 = loaded and verified, 1 = failure (reasons printed).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from core.config_defaults import API_SRID, STORAGE_SRID
from pipeline.errors import AcquisitionError
from pipeline.provenance import read_provenance, utc_now_iso

if TYPE_CHECKING:
    import geopandas as gpd

logger = logging.getLogger("aquamap.pipeline.load")

STAGE = "aquamap_ingest_stage"
OUT_REPORT = "processed/ingestion_report.json"
KEY = ("osm_element", "osm_id", "part_no")
# Representative query locations (lon, lat): Nairobi CBD; a second one is taken from the data itself.
CBD_LONLAT = (36.82, -1.29)


class IngestionError(AcquisitionError):
    """The layer could not be loaded. Nothing was changed (the transaction was rolled back)."""


@dataclass(frozen=True)
class LayerSpec:
    kind: str                                  # spatial_layers.layer_type
    table: str
    pk: str
    file: str                                  # under data/processed/
    layer_name: str
    attrs: dict[str, tuple[str, int]]          # database column -> (source column, max length)
    required: tuple[str, ...] = ()             # database columns that must not be null
    gist_index: str = ""
    has_results: bool = False                  # rows are referenced by road_vulnerability


SPECS: dict[str, LayerSpec] = {
    "roads": LayerSpec("roads", "road_segments", "road_id", "roads.gpkg", "osm_roads_nairobi",
                       {"name": ("name", 255), "highway_type": ("highway", 50), "surface": ("surface", 50)},
                       required=("highway_type",), gist_index="road_segments_geom_gist", has_results=True),
    "rivers": LayerSpec("rivers", "river_centerlines", "river_id", "rivers.gpkg", "osm_rivers_nairobi",
                        {"name": ("name", 255), "waterway": ("waterway", 32)},
                        gist_index="river_centerlines_geom_gist"),
}


@dataclass
class LoadResult:
    kind: str
    layer_id: int | None = None
    source_features: int = 0      # rows that passed cleaning and were offered to the database
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    not_in_source: int = 0        # rows of this layer in the database that the file no longer has
    pruned: int = 0
    results_invalidated: int = 0
    rows_in_database: int = 0
    cleaning: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _column(frame: gpd.GeoDataFrame, column: str) -> list[Any]:
    """Column values as plain Python objects with missing values as None (never NaN)."""
    return [None if v is None or v != v else v for v in frame[column].tolist()]


# ---------------------------------------------------------------------------- prepare (no database)
def prepare(gdf: gpd.GeoDataFrame, spec: LayerSpec) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Clean a source frame into exactly what will be stored. Returns (frame, cleaning log).

    Output columns: osm_element, osm_id, part_no, the layer's attribute columns (database names) and
    ``geometry`` as valid single LineStrings in EPSG:32737.
    """
    import pandas as pd
    import shapely

    from pipeline.geometry import clean_lines

    if gdf.crs is None:
        raise IngestionError(f"{spec.kind}: source has no CRS; refusing to guess one")
    gdf = gdf.copy()
    if "osm_id" not in gdf.columns and "id" in gdf.columns:
        gdf = gdf.rename(columns={"id": "osm_id"})
    if "osm_id" not in gdf.columns:
        raise IngestionError(f"{spec.kind}: source has no 'osm_id' column; it is needed to avoid duplicates")
    if "element" not in gdf.columns:
        gdf["element"] = "way"
    log: dict[str, Any] = {"input_features": int(len(gdf)), "source_crs": gdf.crs.to_string()}

    no_id = gdf["osm_id"].isna()
    log["dropped_no_osm_id"] = int(no_id.sum())
    gdf = gdf[~no_id]
    gdf["element"] = gdf["element"].fillna("way").astype(str).str.slice(0, 8)
    if gdf.crs.to_epsg() != STORAGE_SRID:
        gdf = gdf.to_crs(STORAGE_SRID)
        log["reprojected_to"] = f"EPSG:{STORAGE_SRID}"
    gdf.geometry = shapely.force_2d(gdf.geometry.values)
    gdf, clean_log = clean_lines(gdf)
    log.update({k: v for k, v in clean_log.items() if k != "input_features"})
    # A processed file may hold several rows per OSM id (the parts of a split MultiLineString), so only
    # exact repeats (same element, id and geometry) are duplicates; other rows become numbered parts.
    repeat = gdf.assign(_wkb=shapely.to_wkb(gdf.geometry.values)).duplicated(subset=["element", "osm_id", "_wkb"])
    log["dropped_duplicate_records"] = int(repeat.sum())
    gdf = gdf[~repeat.values]

    out = gdf[["geometry"]].copy()
    out["osm_element"] = gdf["element"].values
    out["osm_id"] = gdf["osm_id"].astype("int64").values
    truncated = 0
    for column, (source, max_len) in spec.attrs.items():
        if source in gdf.columns:
            values = pd.Series([None if v is None or v != v else (str(v).strip() or None) for v in gdf[source].tolist()],
                               index=gdf.index, dtype="object")
        else:
            values = pd.Series([None] * len(gdf), index=gdf.index, dtype="object")
        too_long = values.map(lambda v: v is not None and len(v) > max_len)
        truncated += int(too_long.sum())
        out[column] = pd.Series([v[:max_len] if v is not None else None for v in values], index=out.index, dtype="object")
    log["attribute_values_truncated"] = truncated

    missing = pd.Series(False, index=out.index)
    for column in spec.required:
        missing |= out[column].isna()
    log["dropped_missing_required_attribute"] = int(missing.sum())
    out = out[~missing.values]
    # part number = position of the segment inside its OSM feature, in file order (repeatable)
    out["part_no"] = out.groupby(["osm_element", "osm_id"], sort=False).cumcount().astype("int64")
    out = out.reset_index(drop=True)[[*KEY, *spec.attrs, "geometry"]]
    log["output_features"] = int(len(out))
    return out, log


def read_source(path: Path, spec: LayerSpec) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    import geopandas as gpd

    if not path.exists():
        raise IngestionError(f"{spec.kind}: processed file missing: {path}; run 'python -m pipeline.acquire all'")
    try:
        gdf = gpd.read_file(path)
    except Exception as exc:  # noqa: BLE001 - any reader failure is reported with the file name
        raise IngestionError(f"{spec.kind}: cannot read {path.name}: {type(exc).__name__}: {exc}") from exc
    frame, log = prepare(gdf, spec)
    if frame.empty:
        raise IngestionError(f"{spec.kind}: no usable features in {path.name}; refusing to load an empty layer")
    return frame, log


# ---------------------------------------------------------------------------- database steps
def _stage(conn: Connection, spec: LayerSpec, frame: gpd.GeoDataFrame) -> None:
    """Create the temporary staging table and COPY the cleaned rows into it."""
    import shapely

    attr_cols = "".join(f", {c} text" for c in spec.attrs)
    conn.execute(text(f"DROP TABLE IF EXISTS pg_temp.{STAGE}"))
    conn.execute(text(
        f"CREATE TEMP TABLE {STAGE} (osm_element varchar(8) NOT NULL, osm_id bigint NOT NULL, "
        f"part_no integer NOT NULL{attr_cols}, geom geometry(LineString, {STORAGE_SRID}) NOT NULL) ON COMMIT DROP"
    ))
    ewkb = shapely.to_wkb(shapely.set_srid(frame.geometry.values, STORAGE_SRID), hex=True, include_srid=True)
    columns = [*KEY, *spec.attrs]
    rows = zip(*(_column(frame, c) for c in columns), ewkb.tolist(), strict=True)
    raw = conn.connection.driver_connection  # the psycopg connection of this same transaction
    with raw.cursor() as cur, cur.copy(f"COPY {STAGE} ({', '.join(columns)}, geom) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def _check_stage(conn: Connection, spec: LayerSpec) -> None:
    """PostGIS's own opinion of the staged rows; any problem aborts the load."""
    bad = conn.execute(text(
        f"SELECT count(*) FILTER (WHERE NOT ST_IsValid(geom)) AS invalid, "
        f"count(*) FILTER (WHERE ST_IsEmpty(geom)) AS empty, "
        f"count(*) FILTER (WHERE ST_SRID(geom) <> {STORAGE_SRID}) AS wrong_srid, "
        f"count(*) FILTER (WHERE GeometryType(geom) <> 'LINESTRING') AS wrong_type, "
        f"count(*) - count(DISTINCT (osm_element, osm_id, part_no)) AS duplicate_keys FROM {STAGE}"
    )).mappings().one()
    problems = {k: v for k, v in bad.items() if v}
    if problems:
        examples = conn.execute(text(
            f"SELECT osm_id FROM {STAGE} WHERE NOT ST_IsValid(geom) OR ST_IsEmpty(geom) LIMIT 5")).scalars().all()
        raise IngestionError(f"{spec.kind}: PostGIS rejected staged rows {problems}"
                             + (f"; example osm_id: {examples}" if examples else ""))


def _upsert_layer_row(conn: Connection, spec: LayerSpec, layer_name: str, provenance: dict[str, Any] | None) -> int:
    p = provenance or {}
    return conn.execute(text(
        "INSERT INTO spatial_layers (layer_name, layer_type, source, source_url, license) "
        "VALUES (:n, :t, :s, :u, :l) ON CONFLICT (layer_name, layer_type) DO UPDATE SET "
        "source = EXCLUDED.source, source_url = EXCLUDED.source_url, license = EXCLUDED.license, uploaded_at = now() "
        "RETURNING layer_id"),
        {"n": layer_name, "t": spec.kind, "s": (p.get("source_name") or "unknown")[:100],
         "u": p.get("source_url") or None, "l": (p.get("licence") or "unknown")[:100]}).scalar_one()


def ensure_spatial_index(conn: Connection, spec: LayerSpec) -> None:
    """Create the GiST index if it is missing and fail if it exists but is not usable."""
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS {spec.gist_index} ON {spec.table} USING GIST (geom)"))
    state = conn.execute(text(
        "SELECT i.indisvalid AND i.indisready FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "JOIN pg_am a ON a.oid = c.relam WHERE c.relname = :n AND a.amname = 'gist' "
        "AND i.indrelid = to_regclass(:t)"), {"n": spec.gist_index, "t": spec.table}).scalar()
    if not state:
        raise IngestionError(f"{spec.kind}: spatial index {spec.gist_index} is missing or invalid; "
                             f"run 'REINDEX INDEX {spec.gist_index}'")


def load_layer(conn: Connection, frame: gpd.GeoDataFrame, spec: LayerSpec, *, layer_name: str | None = None,
               provenance: dict[str, Any] | None = None, prune: bool = False) -> LoadResult:
    """Load a prepared frame on ``conn``. The caller owns the transaction (commit = keep, rollback = undo)."""
    result = LoadResult(spec.kind, source_features=len(frame))
    t, cols = spec.table, list(spec.attrs)
    on_key = " AND ".join(f"r.{k} = s.{k}" for k in KEY)
    # one loader at a time per table; readers are not blocked
    conn.execute(text(f"LOCK TABLE {t} IN SHARE ROW EXCLUSIVE MODE"))
    _stage(conn, spec, frame)
    _check_stage(conn, spec)
    result.layer_id = layer_id = _upsert_layer_row(conn, spec, layer_name or spec.layer_name, provenance)

    if spec.has_results:  # a score computed on the old geometry must not survive a geometry change
        result.results_invalidated = conn.execute(text(
            f"DELETE FROM road_vulnerability v USING {t} r, {STAGE} s "
            f"WHERE v.road_id = r.{spec.pk} AND {on_key} AND NOT ST_OrderingEquals(r.geom, s.geom)")).rowcount

    insert_cols = ", ".join(["layer_id", *KEY, *cols, "geom"])
    select_cols = ", ".join([":layer_id", *KEY, *cols, "geom"])
    set_cols = ", ".join(f"{c} = EXCLUDED.{c}" for c in ["layer_id", *cols, "geom"])
    old_vals = ", ".join(f"r.{c}" for c in ["layer_id", *cols])
    new_vals = ", ".join(f"EXCLUDED.{c}" for c in ["layer_id", *cols])
    counts = conn.execute(text(
        f"WITH changed AS (INSERT INTO {t} AS r ({insert_cols}) SELECT {select_cols} FROM {STAGE} "
        f"ON CONFLICT (osm_element, osm_id, part_no) DO UPDATE SET {set_cols} "
        f"WHERE ({old_vals}) IS DISTINCT FROM ({new_vals}) OR NOT ST_OrderingEquals(r.geom, EXCLUDED.geom) "
        f"RETURNING (xmax = 0) AS is_insert) "
        f"SELECT count(*) FILTER (WHERE is_insert), count(*) FILTER (WHERE NOT is_insert) FROM changed"),
        {"layer_id": layer_id}).one()
    result.inserted, result.updated = int(counts[0]), int(counts[1])
    result.unchanged = result.source_features - result.inserted - result.updated

    stale = f"FROM {t} r WHERE r.layer_id = :layer_id AND NOT EXISTS (SELECT 1 FROM {STAGE} s WHERE {on_key})"
    result.not_in_source = int(conn.execute(text(f"SELECT count(*) {stale}"), {"layer_id": layer_id}).scalar_one())
    if prune and result.not_in_source:
        result.pruned = conn.execute(text(f"DELETE {stale}"), {"layer_id": layer_id}).rowcount

    result.rows_in_database = int(conn.execute(
        text(f"SELECT count(*) FROM {t} WHERE layer_id = :layer_id"), {"layer_id": layer_id}).scalar_one())
    conn.execute(text("UPDATE spatial_layers SET feature_count = :n WHERE layer_id = :layer_id"),
                 {"n": result.rows_in_database, "layer_id": layer_id})
    ensure_spatial_index(conn, spec)
    conn.execute(text(f"DROP TABLE IF EXISTS pg_temp.{STAGE}"))
    conn.execute(text(f"ANALYZE {t}"))
    return result


def load_file(path: Path, kind: str, engine: Engine, *, layer_name: str | None = None, prune: bool = False) -> LoadResult:
    """Read, clean and load one processed file inside a single transaction."""
    spec = SPECS[kind]
    frame, log = read_source(path, spec)
    try:
        with engine.begin() as conn:  # commit on success, rollback on any exception
            result = load_layer(conn, frame, spec, layer_name=layer_name, provenance=read_provenance(path), prune=prune)
    except SQLAlchemyError as exc:
        detail = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0]
        raise IngestionError(f"{kind}: database error ({type(exc).__name__}): {detail}. "
                             "The transaction was rolled back; nothing was changed.") from exc
    except IngestionError:
        raise
    except Exception as exc:  # e.g. psycopg errors raised during COPY
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        raise IngestionError(f"{kind}: load failed ({type(exc).__name__}): {detail}. "
                             "The transaction was rolled back; nothing was changed.") from exc
    result.cleaning = log
    return result


def load_roads(gpkg_path: Path, engine: Engine, **kwargs: Any) -> LoadResult:
    """Clean and load roads (all OSM highway classes). Safe to re-run."""
    return load_file(gpkg_path, "roads", engine, **kwargs)


def load_rivers(gpkg_path: Path, engine: Engine, **kwargs: Any) -> LoadResult:
    """Clean and load river centrelines. Safe to re-run."""
    return load_file(gpkg_path, "rivers", engine, **kwargs)


# ---------------------------------------------------------------------------- verification
def _query_points(frame: gpd.GeoDataFrame) -> dict[str, Any]:
    """Two test locations in EPSG:32737: the CBD and the midpoint of the median-length feature."""
    from pyproj import Transformer
    from shapely.geometry import Point

    x, y = Transformer.from_crs(API_SRID, STORAGE_SRID, always_xy=True).transform(*CBD_LONLAT)
    median = frame.geometry.iloc[int(frame.geometry.length.argsort().iloc[len(frame) // 2])]
    return {"cbd": Point(x, y), "data_midpoint": median.interpolate(0.5, normalized=True)}


def verify_layer(conn: Connection, frame: gpd.GeoDataFrame, spec: LayerSpec, *, layer_name: str | None = None,
                 allow_extra: bool = True) -> dict[str, Any]:
    """Compare the database layer with the prepared source frame. Returns {'ok', 'errors', 'warnings', 'stats'}."""
    import numpy as np
    import shapely
    from shapely.geometry import box

    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {"source_features": int(len(frame))}
    t, cols, name = spec.table, list(spec.attrs), layer_name or spec.layer_name
    layer_id = conn.execute(text("SELECT layer_id FROM spatial_layers WHERE layer_name = :n AND layer_type = :t"),
                            {"n": name, "t": spec.kind}).scalar()
    if layer_id is None:
        return {"ok": False, "errors": [f"layer '{name}' has not been loaded"], "warnings": [], "stats": stats}
    p = {"layer_id": layer_id}

    # --- counts, SRID, validity, type, duplicates
    agg = conn.execute(text(
        f"SELECT count(*) AS n, count(*) FILTER (WHERE NOT ST_IsValid(geom)) AS invalid, "
        f"count(*) FILTER (WHERE ST_IsEmpty(geom)) AS empty, array_agg(DISTINCT ST_SRID(geom)) AS srids, "
        f"array_agg(DISTINCT GeometryType(geom)) AS types, coalesce(sum(length_m), 0) AS length_m, "
        f"count(*) - count(DISTINCT (osm_element, osm_id, part_no)) AS duplicate_keys, "
        f"count(*) - count(DISTINCT ST_AsEWKB(geom)) AS duplicate_geometries, "
        f"ST_XMin(ST_Extent(geom)) AS x0, ST_YMin(ST_Extent(geom)) AS y0, "
        f"ST_XMax(ST_Extent(geom)) AS x1, ST_YMax(ST_Extent(geom)) AS y1 FROM {t} WHERE layer_id = :layer_id"),
        p).mappings().one()
    typmod = conn.execute(text("SELECT type, srid FROM geometry_columns WHERE f_table_name = :t AND "
                               "f_geometry_column = 'geom' AND f_table_schema = current_schema()"), {"t": t}).one()
    stats.update(database_features=int(agg["n"]), invalid=int(agg["invalid"]), empty=int(agg["empty"]),
                 srids=sorted(agg["srids"] or []), geometry_types=sorted(agg["types"] or []),
                 column_definition=f"{typmod[0]}, EPSG:{typmod[1]}", duplicate_keys=int(agg["duplicate_keys"]),
                 duplicate_geometries_database=int(agg["duplicate_geometries"]),
                 duplicate_geometries_source=int(len(frame) - len(set(shapely.to_wkb(frame.geometry.values).tolist()))))
    if agg["invalid"] or agg["empty"]:
        errors.append(f"{agg['invalid']} invalid and {agg['empty']} empty geometries in the database")
    if stats["srids"] != [STORAGE_SRID] or typmod[1] != STORAGE_SRID:
        errors.append(f"SRIDs in the database are {stats['srids']} (column {typmod[1]}), expected only {STORAGE_SRID}")
    if stats["geometry_types"] != ["LINESTRING"]:
        errors.append(f"geometry types are {stats['geometry_types']}, expected only LINESTRING")
    if agg["duplicate_keys"]:
        errors.append(f"{agg['duplicate_keys']} duplicate (osm_element, osm_id, part_no) keys")
    if stats["duplicate_geometries_database"] != stats["duplicate_geometries_source"] and int(agg["n"]) == len(frame):
        errors.append("the database holds a different number of repeated geometries than the source")

    # --- row by row: keys, attributes, geometry
    db_rows = {(r[0], r[1], r[2]): r[3:] for r in conn.execute(text(
        f"SELECT osm_element, osm_id, part_no, {', '.join(cols)}, ST_AsBinary(geom) FROM {t} "
        f"WHERE layer_id = :layer_id"), p)}
    src_keys = list(zip(*(_column(frame, k) for k in KEY), strict=True))
    src_attrs = list(zip(*(_column(frame, c) for c in cols), strict=True))
    missing = [k for k in src_keys if k not in db_rows]
    extra = len(db_rows) - (len(src_keys) - len(missing))
    stats.update(missing_in_database=len(missing), in_database_not_in_source=extra)
    if missing:
        errors.append(f"{len(missing)} source features are not in the database, e.g. {missing[:3]}")
    if extra:
        (warnings if allow_extra else errors).append(
            f"{extra} database rows of this layer are not in the source file (re-run with --prune to remove them)")
    attr_diff = geom_diff = 0
    have = [i for i, k in enumerate(src_keys) if k in db_rows]
    if have:
        db_geoms = shapely.from_wkb([bytes(db_rows[src_keys[i]][-1]) for i in have])
        same = shapely.equals_exact(db_geoms, frame.geometry.values[have], tolerance=0.0)
        geom_diff = int((~same).sum())
        attr_diff = sum(1 for i in have if tuple(db_rows[src_keys[i]][:-1]) != src_attrs[i])
    stats.update(attribute_mismatches=attr_diff, geometry_mismatches=geom_diff)
    if attr_diff:
        errors.append(f"{attr_diff} rows have attributes that differ from the source")
    if geom_diff:
        errors.append(f"{geom_diff} rows have coordinates that differ from the source")

    src_len = float(frame.geometry.length.sum())
    stats.update(total_length_km_source=round(src_len / 1000, 3), total_length_km_database=round(float(agg["length_m"]) / 1000, 3))
    if not extra and abs(src_len - float(agg["length_m"])) > 1e-6 * max(src_len, 1.0):
        errors.append("total length in the database differs from the source")
    if not extra and agg["n"]:
        b = [float(agg[k]) for k in ("x0", "y0", "x1", "y1")]
        stats["extent_database"] = [round(v, 3) for v in b]
        if not np.allclose(b, frame.total_bounds, atol=2.0):  # ST_Extent is a float4 box: about 1 m precision here
            errors.append(f"database extent {b} differs from source extent {list(frame.total_bounds)}")
    for column in cols:
        stats[f"null_{column}_source"] = int(frame[column].isna().sum())
        stats[f"null_{column}_database"] = int(conn.execute(
            text(f"SELECT count(*) FROM {t} WHERE layer_id = :layer_id AND {column} IS NULL"), p).scalar_one())

    # --- representative spatial queries, answered independently by PostGIS and by GeoPandas
    queries: list[dict[str, Any]] = []
    comparable = not extra and not missing
    lyr = f"FROM {t} WHERE layer_id = :layer_id"
    for label, pt in _query_points(frame).items():
        q = {"x": pt.x, "y": pt.y, **p}
        pt_sql = f"ST_SetSRID(ST_MakePoint(:x, :y), {STORAGE_SRID})"
        env_sql = f"ST_MakeEnvelope(:x - 1000, :y - 1000, :x + 1000, :y + 1000, {STORAGE_SRID})"
        dist = frame.geometry.distance(pt)
        checks = {
            "bbox_2km_intersects": (
                int(conn.execute(text(f"SELECT count(*) {lyr} AND geom && {env_sql} AND ST_Intersects(geom, {env_sql})"), q).scalar_one()),
                int(frame.geometry.intersects(box(pt.x - 1000, pt.y - 1000, pt.x + 1000, pt.y + 1000)).sum())),
            "within_500m": (
                int(conn.execute(text(f"SELECT count(*) {lyr} AND ST_DWithin(geom, {pt_sql}, 500)"), q).scalar_one()),
                int((dist <= 500).sum())),
        }
        nearest = conn.execute(text(
            f"SELECT osm_id, ST_Distance(geom, {pt_sql}) {lyr} ORDER BY geom <-> {pt_sql} LIMIT 1"), q).one_or_none()
        entry: dict[str, Any] = {"location": label, "x": round(pt.x, 2), "y": round(pt.y, 2)}
        for check, (in_db, in_src) in checks.items():
            entry[check] = {"database": in_db, "source": in_src}
            if comparable and in_db != in_src:
                errors.append(f"query {check} at {label}: database {in_db}, source {in_src}")
        if nearest is not None:
            entry["nearest_m"] = {"database": round(float(nearest[1]), 6), "source": round(float(dist.min()), 6),
                                  "osm_id": int(nearest[0])}
            if comparable and abs(float(nearest[1]) - float(dist.min())) > 1e-6:
                errors.append(f"nearest-feature distance at {label}: database {nearest[1]}, source {dist.min()}")
        # the same point given in lon/lat must land on the same features (checks the CRS end to end)
        if label == "cbd":
            lonlat = int(conn.execute(text(
                f"SELECT count(*) {lyr} AND ST_DWithin(geom, ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), "
                f"{API_SRID}), {STORAGE_SRID}), 500)"), {"lon": CBD_LONLAT[0], "lat": CBD_LONLAT[1], **p}).scalar_one())
            entry["within_500m_from_lonlat"] = lonlat
            if lonlat != checks["within_500m"][0]:
                errors.append("a lon/lat query point does not give the same result as its EPSG:32737 equivalent")
        queries.append(entry)
    stats["spatial_queries"] = queries

    # --- spatial index: exists, valid, and the planner can use it for a bounding-box query
    try:
        ensure_index_ok = conn.execute(text(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_am a ON a.oid = c.relam "
            "WHERE c.relname = :n AND a.amname = 'gist' AND i.indrelid = to_regclass(:t)"),
            {"n": spec.gist_index, "t": t}).scalar()
        stats["gist_index"] = spec.gist_index if ensure_index_ok else None
        if not ensure_index_ok:
            errors.append(f"spatial index {spec.gist_index} is missing or invalid")
        else:
            pt = _query_points(frame)["cbd"]
            with conn.begin_nested():
                conn.execute(text("SET LOCAL enable_seqscan = off"))
                plan = "\n".join(r[0] for r in conn.execute(text(
                    f"EXPLAIN SELECT 1 FROM {t} WHERE geom && ST_MakeEnvelope(:x - 1000, :y - 1000, :x + 1000, "
                    f":y + 1000, {STORAGE_SRID})"), {"x": pt.x, "y": pt.y}))
                conn.execute(text("SET LOCAL enable_seqscan = on"))
            stats["bbox_query_uses_gist_index"] = spec.gist_index in plan
            if spec.gist_index not in plan:
                warnings.append("the planner did not choose the GiST index for a bounding-box query")
    except SQLAlchemyError as exc:
        errors.append(f"could not inspect the spatial index: {type(exc).__name__}")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


# ---------------------------------------------------------------------------- command line
def run(data_dir: Path, engine: Engine, *, kinds: tuple[str, ...] = ("roads", "rivers"), prune: bool = False,
        verify_only: bool = False) -> dict[str, Any]:
    """Load (unless ``verify_only``) and verify each layer; write the report. Layers fail independently."""
    report: dict[str, Any] = {"generated_at": utc_now_iso(), "storage_crs": f"EPSG:{STORAGE_SRID}", "layers": {}}
    for kind in kinds:
        spec, entry = SPECS[kind], {"file": f"processed/{SPECS[kind].file}"}
        report["layers"][kind] = entry
        path = data_dir / "processed" / spec.file
        try:
            if not verify_only:
                entry["load"] = load_file(path, kind, engine, prune=prune).to_dict()
            frame, _ = read_source(path, spec)
            with engine.connect() as conn:
                entry["verify"] = verify_layer(conn, frame, spec)
        except IngestionError as exc:
            entry["error"] = str(exc)
        except SQLAlchemyError as exc:
            entry["error"] = f"{kind}: database error during verification ({type(exc).__name__})"
        entry["ok"] = "error" not in entry and entry["verify"]["ok"]
    report["all_ok"] = all(e["ok"] for e in report["layers"].values())
    out = data_dir / OUT_REPORT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def _print(report: dict[str, Any]) -> None:
    for kind, e in report["layers"].items():
        print(f"\n[{'PASS' if e['ok'] else 'FAIL'}] {kind}  ({e['file']})")
        if "error" in e:
            print(f"    ERROR {e['error']}", file=sys.stderr)
            continue
        if "load" in e:
            ld = e["load"]
            print(f"    loaded: {ld['source_features']} offered, {ld['inserted']} inserted, {ld['updated']} updated, "
                  f"{ld['unchanged']} unchanged, {ld['pruned']} pruned, {ld['rows_in_database']} now in the database")
            dropped = {k: v for k, v in ld["cleaning"].items() if (k.startswith("dropped") or k.startswith("repaired")) and v}
            print(f"    cleaning: {ld['cleaning']['input_features']} read, changes: {dropped or 'none'}")
        s = e["verify"]["stats"]
        for key in ("source_features", "database_features", "missing_in_database", "in_database_not_in_source",
                    "geometry_mismatches", "attribute_mismatches", "invalid", "empty", "srids", "geometry_types",
                    "duplicate_keys", "total_length_km_source", "total_length_km_database", "gist_index",
                    "bbox_query_uses_gist_index"):
            if key in s:
                print(f"    {key}: {s[key]}")
        for q in s.get("spatial_queries", []):
            print(f"    query@{q['location']}: " + ", ".join(f"{k}={v}" for k, v in q.items() if k not in ("location", "x", "y")))
        for w in e["verify"]["warnings"]:
            print(f"    WARNING {w}")
        for err in e["verify"]["errors"]:
            print(f"    ERROR {err}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.load_vectors", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    p.add_argument("--only", choices=tuple(SPECS), help="load one layer instead of both")
    p.add_argument("--prune", action="store_true", help="delete rows of the layer that are no longer in the file")
    p.add_argument("--verify-only", action="store_true", help="compare database and files without loading")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        from app.db import get_engine

        engine = get_engine()
    except Exception as exc:  # noqa: BLE001 - configuration problems; never echo connection details
        print(f"FAILED: database is not configured ({type(exc).__name__}); check .env", file=sys.stderr)
        return 1
    report = run(args.data_dir, engine, kinds=(args.only,) if args.only else tuple(SPECS), prune=args.prune,
                 verify_only=args.verify_only)
    _print(report)
    print(f"\nreport written to {args.data_dir / OUT_REPORT}")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
