"""Raw -> processed vectors. Raw files are never modified; processed copies go to ``data/processed/``.

Only mechanical clean-up is done, and every drop is counted in the provenance record:
keep line geometries, drop null/empty geometries, repair invalid lines (``make_valid``, line parts only;
unrepairable ones are dropped), drop zero-length parts, drop repeated OSM ids,
keep the columns the system uses, rename ``id`` to ``osm_id`` and reproject to EPSG:32737.
No road-class filtering and no attribute guessing happens here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config_defaults import STORAGE_SRID
from pipeline.errors import AcquisitionError
from pipeline.geometry import clean_lines
from pipeline.provenance import Provenance, read_provenance, write_provenance

KEEP = {"roads": ["element", "osm_id", "name", "highway", "surface"], "rivers": ["element", "osm_id", "name", "waterway"]}
OUT_NAME = {"roads": "roads.gpkg", "rivers": "rivers.gpkg"}


def process_vector(raw_path: Path, kind: str, processed_dir: Path, data_root: Path | None = None) -> Path:
    import geopandas as gpd

    if kind not in KEEP:
        raise ValueError(kind)
    if not raw_path.exists():
        raise AcquisitionError(f"raw {kind} file missing: {raw_path}; run the fetch step first")
    gdf = gpd.read_file(raw_path)
    if gdf.crs is None:
        raise AcquisitionError(f"{raw_path.name} has no CRS; cannot reproject safely")
    log: dict[str, Any] = {"input_features": len(gdf)}

    before = len(gdf)
    id_keys = [c for c in ("element", "id") if c in gdf.columns]
    if id_keys:
        gdf = gdf.drop_duplicates(subset=id_keys)
    log["dropped_duplicate_ids"] = before - len(gdf)
    gdf = gdf.rename(columns={"id": "osm_id"})
    gdf = gdf[[c for c in KEEP[kind] if c in gdf.columns] + ["geometry"]]
    gdf, clean_log = clean_lines(gdf)  # validate, repair, split multi-lines; every drop is counted
    log.update({k: v for k, v in clean_log.items() if k not in ("input_features", "output_features")})
    gdf = gdf.to_crs(STORAGE_SRID).reset_index(drop=True)
    log["output_features"] = len(gdf)

    processed_dir.mkdir(parents=True, exist_ok=True)
    out = processed_dir / OUT_NAME[kind]
    tmp = out.with_suffix(".part.gpkg")
    gdf.to_file(tmp, driver="GPKG")
    tmp.replace(out)

    raw_rec = read_provenance(raw_path) or {}
    rec = Provenance(kind, raw_rec.get("source_name", "unknown (raw file has no provenance record)"),
                     raw_rec.get("source_url", ""), raw_rec.get("licence", "unknown"),
                     raw_rec.get("attribution", ""), "pipeline.process_vectors",
                     parameters={"kept_columns": list(gdf.columns)},
                     limitations=raw_rec.get("limitations", []), feature_count=len(gdf),
                     crs=f"EPSG:{STORAGE_SRID}", stage="processed",
                     derived_from=[raw_path.name], processing_log=log)
    rec.add_file(out, data_root or processed_dir.parent)
    write_provenance(rec, out)
    return out
