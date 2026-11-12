"""Steps 5-7: measure every eligible road, apply ``core.ivi`` and store the results in PostGIS.

    python -m pipeline.score_roads                 # score with the active configuration
    python -m pipeline.score_roads --all-classes   # also score footways, paths, tracks ...
    python -m pipeline.score_roads --verify-only   # re-check what is stored, change nothing
    python -m pipeline.score_roads --new-config    # store the code defaults as a new active configuration

Eligible roads are the rows of ``road_segments`` whose ``highway_type`` is one of
``core.config_defaults.DEFAULT_HIGHWAY_CLASSES`` (the vehicular network, design decision A3).

Each run: (1) reads roads and rivers from PostGIS, (2) samples elevation and slope along every road
and measures the distance to the nearest river, all in EPSG:32737, (3) scores every road with the
active ``scoring_config``, (4) writes ``road_vulnerability`` in ONE transaction (upsert; results of
roads that are no longer eligible are removed), and (5) records the run in ``pipeline_runs`` with the
model version, configuration version, inputs and counts. A failed run is recorded as ``failed`` and
leaves the previous results untouched.

The IVI is a relative vulnerability index on a 1-5 scale, not a flood probability or measured risk.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from core import ivi
from core.config_defaults import DEFAULT_HIGHWAY_CLASSES, MODEL_VERSION, STORAGE_SRID, TIER_NAMES
from core.ivi import ScoringConfig
from pipeline.errors import AcquisitionError
from pipeline.provenance import read_provenance, utc_now_iso

if TYPE_CHECKING:
    import geopandas as gpd

STAGE = "aquamap_score_stage"
OUT_REPORT = "processed/scoring_report.json"
DEM_FILE = "processed/dem_32737.tif"
SLOPE_FILE = "processed/slope_pct_32737.tif"
MEASURES = ("elevation_m", "slope_pct", "river_dist_m")
SCORES = ("score_elevation", "score_slope", "score_river", "score_surface")
RESULT_COLUMNS = ("road_id", *MEASURES, *SCORES, "vulnerability_index", "risk_category", "data_flags", "terrain_samples")


class ScoringError(AcquisitionError):
    """Scoring could not be completed. Stored results were not changed."""


# ---------------------------------------------------------------------------- pure scoring of a table
def score_segments(roads: gpd.GeoDataFrame, config: ScoringConfig) -> gpd.GeoDataFrame:
    """Add factor scores, ``vulnerability_index``, ``risk_category`` and ``data_flags`` to measured roads.

    Needs the columns ``elevation_m``, ``slope_pct``, ``river_dist_m`` and ``surface`` (NaN/None = missing).
    """
    import pandas as pd

    ivi.validate_config(config)
    missing = [c for c in (*MEASURES, "surface") if c not in roads.columns]
    if missing:
        raise ScoringError(f"roads are missing the columns {missing}; run the measurement step first")
    results = [ivi.score_segment(e, s, d, m, config) for e, s, d, m in
               zip(roads["elevation_m"].tolist(), roads["slope_pct"].tolist(), roads["river_dist_m"].tolist(),
                   roads["surface"].tolist(), strict=True)]
    out = roads.copy()
    for column, attr in zip(SCORES, ("score_elevation", "score_slope", "score_river", "score_surface"), strict=True):
        out[column] = pd.Series([getattr(r, attr) for r in results], index=out.index, dtype="float64")
    out["vulnerability_index"] = pd.Series([r.ivi for r in results], index=out.index, dtype="float64")
    out["risk_category"] = pd.Series([r.tier for r in results], index=out.index, dtype="object")
    out["data_flags"] = pd.Series([list(r.flags) for r in results], index=out.index, dtype="object")
    return out


def summarise(scored: gpd.GeoDataFrame) -> dict[str, Any]:
    """Counts and lengths by tier, flag counts and distribution of the index."""
    has = scored["vulnerability_index"].notna()
    length = scored.geometry.length if "length_m" not in scored.columns else scored["length_m"]
    by_tier = {t: {"segments": int((scored["risk_category"] == t).sum()),
                   "length_km": round(float(length[scored["risk_category"] == t].sum()) / 1000, 1)} for t in TIER_NAMES}
    flags: dict[str, int] = {}
    for item in scored["data_flags"]:
        for flag in item:
            flags[flag] = flags.get(flag, 0) + 1
    out: dict[str, Any] = {"segments": int(len(scored)), "segments_scored": int(has.sum()),
                           "segments_without_index": int((~has).sum()),
                           "segments_flagged": int(sum(1 for f in scored["data_flags"] if f)),
                           "by_tier": by_tier, "flags": dict(sorted(flags.items()))}
    if has.any():
        v = scored.loc[has, "vulnerability_index"]
        out["index"] = {"min": float(v.min()), "mean": round(float(v.mean()), 3), "median": float(v.median()),
                        "max": float(v.max())}
    return out


# ---------------------------------------------------------------------------- database: configuration
def load_active_config(conn: Connection) -> tuple[int, ScoringConfig] | None:
    row = conn.execute(text("SELECT config_version, weights, breakpoints, surface_map, tier_thresholds "
                            "FROM scoring_config WHERE is_active")).one_or_none()
    if row is None:
        return None
    try:
        return int(row[0]), ScoringConfig.from_storage(row[1], row[2], row[3], row[4])
    except (ValueError, KeyError, TypeError) as exc:
        raise ScoringError(f"active scoring configuration (version {row[0]}) is not valid for the 1-5 model: {exc}. "
                           "Run with --new-config to store the documented defaults as a new active version.") from exc


def store_config(conn: Connection, config: ScoringConfig) -> int:
    """Insert ``config`` as a new version and make it the only active one."""
    ivi.validate_config(config)
    stored = config.to_storage()
    conn.execute(text("UPDATE scoring_config SET is_active = FALSE WHERE is_active"))
    return int(conn.execute(text(
        "INSERT INTO scoring_config (weights, breakpoints, surface_map, tier_thresholds, is_active) VALUES "
        "(CAST(:w AS jsonb), CAST(:b AS jsonb), CAST(:s AS jsonb), CAST(:t AS jsonb), TRUE) RETURNING config_version"),
        {"w": json.dumps(stored["weights"]), "b": json.dumps(stored["breakpoints"]),
         "s": json.dumps(stored["surface_map"]), "t": json.dumps(stored["tier_thresholds"])}).scalar_one())


def ensure_config(engine: Engine, *, new: bool = False) -> tuple[int, ScoringConfig]:
    """The active configuration; the documented defaults are stored first if there is none (or ``new``)."""
    with engine.begin() as conn:
        if not new:
            active = load_active_config(conn)
            if active is not None:
                return active
        config = ScoringConfig()
        return store_config(conn, config), config


# ---------------------------------------------------------------------------- database: inputs
def read_inputs(conn: Connection, classes: tuple[str, ...] | None) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[str, int]]:
    """Roads to score (filtered by class unless ``classes`` is None), all rivers, and counts."""
    import geopandas as gpd

    where = "" if classes is None else "WHERE highway_type = ANY(:classes)"
    roads = gpd.read_postgis(text(f"SELECT road_id, highway_type, surface, length_m, geom FROM road_segments {where} "
                                  "ORDER BY road_id"), conn, geom_col="geom",
                             params=None if classes is None else {"classes": list(classes)})
    rivers = gpd.read_postgis(text("SELECT river_id, geom FROM river_centerlines ORDER BY river_id"), conn, geom_col="geom")
    total = int(conn.execute(text("SELECT count(*) FROM road_segments")).scalar_one())
    for frame in (roads, rivers):
        if frame.crs is None:
            frame.set_crs(STORAGE_SRID, inplace=True)
    return roads, rivers, {"roads_in_database": total, "roads_eligible": int(len(roads)),
                           "roads_not_eligible": total - int(len(roads)), "rivers": int(len(rivers))}


# ---------------------------------------------------------------------------- database: results
def _clean(value: Any) -> Any:
    return None if value is None or (isinstance(value, float) and value != value) else value


def persist_scores(conn: Connection, scored: gpd.GeoDataFrame, config_version: int, *, run_id: int | None = None,
                   model_version: str = MODEL_VERSION) -> dict[str, int]:
    """Replace the current results with ``scored`` on ``conn`` (the caller owns the transaction).

    Upserts one row per road and deletes results of roads that are not in ``scored`` (no longer
    eligible), so the table always describes exactly one run. Returns counts.
    """
    conn.execute(text("LOCK TABLE road_vulnerability IN SHARE ROW EXCLUSIVE MODE"))
    conn.execute(text(f"DROP TABLE IF EXISTS pg_temp.{STAGE}"))
    conn.execute(text(
        f"CREATE TEMP TABLE {STAGE} (road_id bigint PRIMARY KEY, elevation_m real, slope_pct real, river_dist_m real, "
        "score_elevation real, score_slope real, score_river real, score_surface real, "
        "vulnerability_index numeric(4,3), risk_category varchar(8), data_flags text[] NOT NULL, "
        "terrain_samples integer) ON COMMIT DROP"))
    columns = [scored[c].tolist() for c in RESULT_COLUMNS]
    raw = conn.connection.driver_connection
    with raw.cursor() as cur, cur.copy(f"COPY {STAGE} ({', '.join(RESULT_COLUMNS)}) FROM STDIN") as copy:
        for row in zip(*columns, strict=True):
            copy.write_row([_clean(v) for v in row])

    orphans = conn.execute(text(f"SELECT count(*) FROM {STAGE} s LEFT JOIN road_segments r USING (road_id) "
                                "WHERE r.road_id IS NULL")).scalar_one()
    if orphans:
        raise ScoringError(f"{orphans} scored roads no longer exist in road_segments (was the table reloaded "
                           "during scoring?); run again")
    data_cols = RESULT_COLUMNS[1:]
    meta = {"cfg": config_version, "run": run_id, "model": model_version}
    upserted = conn.execute(text(
        f"INSERT INTO road_vulnerability (road_id, {', '.join(data_cols)}, config_version, run_id, model_version, computed_at) "
        f"SELECT road_id, {', '.join(data_cols)}, :cfg, :run, :model, now() FROM {STAGE} "
        f"ON CONFLICT (road_id) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in data_cols)}, "
        "config_version = EXCLUDED.config_version, run_id = EXCLUDED.run_id, model_version = EXCLUDED.model_version, "
        "computed_at = EXCLUDED.computed_at"), meta).rowcount
    removed = conn.execute(text(
        f"DELETE FROM road_vulnerability v WHERE NOT EXISTS (SELECT 1 FROM {STAGE} s WHERE s.road_id = v.road_id)")).rowcount
    conn.execute(text(f"DROP TABLE IF EXISTS pg_temp.{STAGE}"))
    conn.execute(text("ANALYZE road_vulnerability"))
    return {"results_written": int(upserted), "results_removed_not_eligible": int(removed)}


def verify_results(conn: Connection, config_version: int, expected_segments: int | None = None) -> dict[str, Any]:
    """Re-derive every stored index inside PostgreSQL and check consistency. Returns {'ok','errors','stats'}."""
    errors: list[str] = []
    cfg = conn.execute(text("SELECT weights, tier_thresholds FROM scoring_config WHERE config_version = :c"),
                       {"c": config_version}).one_or_none()
    if cfg is None:
        return {"ok": False, "errors": [f"configuration version {config_version} does not exist"], "stats": {}}
    w, t = cfg[0], cfg[1]
    p = {"we": w["elevation"], "ws": w["slope"], "wd": w["river"], "wm": w["surface"],
         "medium": t["Medium"], "high": t["High"], "severe": t["Severe"], "cfg": config_version}
    weighted = ("round((:we * score_elevation + :ws * score_slope + :wd * score_river + :wm * score_surface)::numeric, 3)")
    tier = ("CASE WHEN vulnerability_index >= :severe THEN 'Severe' WHEN vulnerability_index >= :high THEN 'High' "
            "WHEN vulnerability_index >= :medium THEN 'Medium' ELSE 'Low' END")
    row = conn.execute(text(
        f"SELECT count(*) AS results, count(vulnerability_index) AS scored, "
        f"count(*) FILTER (WHERE vulnerability_index IS NOT NULL AND abs(vulnerability_index - {weighted}) > 0.001) AS formula_mismatch, "
        f"count(*) FILTER (WHERE vulnerability_index IS NOT NULL AND risk_category <> {tier}) AS tier_mismatch, "
        "count(*) FILTER (WHERE vulnerability_index IS NULL AND NOT (data_flags && ARRAY['no_dem','no_slope'])) AS unexplained_null, "
        "count(*) FILTER (WHERE vulnerability_index IS NOT NULL AND (score_elevation IS NULL OR score_slope IS NULL "
        "OR score_river IS NULL OR score_surface IS NULL)) AS index_without_factor, "
        "count(*) FILTER (WHERE config_version <> :cfg) AS other_config, "
        "count(*) FILTER (WHERE model_version IS NULL OR computed_at IS NULL OR run_id IS NULL) AS missing_metadata, "
        "count(DISTINCT run_id) AS runs, count(DISTINCT model_version) AS models, "
        "min(vulnerability_index) AS ivi_min, max(vulnerability_index) AS ivi_max, "
        "min(computed_at) AS computed_from, max(computed_at) AS computed_to "
        "FROM road_vulnerability"), p).mappings().one()
    stats = {k: (float(v) if k.startswith("ivi_") and v is not None else v) for k, v in row.items()}
    for key, message in (("formula_mismatch", "stored indexes differ from the weighted sum of their factor scores"),
                         ("tier_mismatch", "stored tiers do not match the thresholds"),
                         ("unexplained_null", "results without an index and without a flag explaining why"),
                         ("index_without_factor", "indexes stored although a factor score is missing"),
                         ("other_config", "results computed with another configuration version"),
                         ("missing_metadata", "results without model version, run or analysis date")):
        if row[key]:
            errors.append(f"{row[key]} {message}")
    if row["results"] and (row["runs"] != 1 or row["models"] != 1):
        errors.append(f"results come from {row['runs']} runs and {row['models']} model versions; expected one of each")
    if expected_segments is not None and row["results"] != expected_segments:
        errors.append(f"{row['results']} results stored, expected {expected_segments}")
    return {"ok": not errors, "errors": errors, "stats": stats}


# ---------------------------------------------------------------------------- orchestration
def _start_run(engine: Engine, config_version: int, model_version: str) -> int:
    with engine.begin() as conn:
        return int(conn.execute(text("INSERT INTO pipeline_runs (config_version, status, model_version) "
                                     "VALUES (:c, 'running', :m) RETURNING run_id"),
                                {"c": config_version, "m": model_version}).scalar_one())


def _fail_run(engine: Engine, run_id: int, message: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE pipeline_runs SET status = 'failed', finished_at = now(), error = :e "
                              "WHERE run_id = :r"), {"e": message[:2000], "r": run_id})
    except SQLAlchemyError:
        pass  # the original error is what matters; it is raised by the caller


def _raster_inputs(data_dir: Path) -> dict[str, Any]:
    out = {}
    for name, rel in (("dem", DEM_FILE), ("slope", SLOPE_FILE)):
        record = read_provenance(data_dir / rel) or {}
        files = record.get("files") or [{}]
        out[name] = {"file": rel, "sha256": files[0].get("sha256"), "source": record.get("source_name")}
    return out


def run(data_dir: Path, engine: Engine, *, all_classes: bool = False, step_m: float | None = None,
        new_config: bool = False) -> dict[str, Any]:
    """Measure, score and store. Returns the report (also written to ``data/processed/scoring_report.json``)."""
    from pipeline import compute_metrics as cm

    step = step_m or cm.DEFAULT_STEP_M
    classes = None if all_classes else DEFAULT_HIGHWAY_CLASSES
    config_version, config = ensure_config(engine, new=new_config)
    run_id = _start_run(engine, config_version, MODEL_VERSION)
    try:
        with engine.connect() as conn:
            roads, rivers, counts = read_inputs(conn, classes)
        if roads.empty:
            raise ScoringError("no eligible roads in the database; run 'python -m pipeline.load_vectors' first")
        measured = cm.add_river_distance(
            cm.add_elevation_and_slope(roads, data_dir / DEM_FILE, data_dir / SLOPE_FILE, step), rivers)
        scored = score_segments(measured, config)
        summary = summarise(scored)
        inputs = {
            "model_version": MODEL_VERSION, "config_version": config_version, "formula": "IVI = WE*E + WS*S + WD*D + WM*M",
            "scale": "1-5 (5 = most vulnerable)", "weights": dict(config.weights), "interpretation": ivi.INTERPRETATION_NOTE,
            "eligible_highway_classes": "all" if classes is None else list(classes), "terrain_sample_step_m": step,
            "terrain_samples_total": int(measured["terrain_samples"].sum()), "crs": f"EPSG:{STORAGE_SRID}",
            "rasters": _raster_inputs(data_dir), **counts,
        }
        with engine.begin() as conn:  # results and the run record succeed or fail together
            written = persist_scores(conn, scored, config_version, run_id=run_id)
            check = verify_results(conn, config_version, expected_segments=len(scored))
            if not check["ok"]:
                raise ScoringError("stored results failed verification: " + "; ".join(check["errors"]))
            conn.execute(text(
                "UPDATE pipeline_runs SET status = 'succeeded', finished_at = now(), segments_scored = :s, "
                "segments_flagged = :f, input_summary = CAST(:i AS jsonb) WHERE run_id = :r"),
                {"s": summary["segments_scored"], "f": summary["segments_flagged"],
                 "i": json.dumps({**inputs, "summary": summary}), "r": run_id})
    except Exception as exc:
        detail = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        _fail_run(engine, run_id, f"{type(exc).__name__}: {detail}")
        if isinstance(exc, AcquisitionError):
            raise
        raise ScoringError(f"scoring failed ({type(exc).__name__}): {detail}. Previous results were not changed.") from exc
    report = {"generated_at": utc_now_iso(), "run_id": run_id, "ok": True, **inputs, **written,
              "summary": summary, "verification": check["stats"]}
    _write_report(data_dir, report)
    return report


def _write_report(data_dir: Path, report: dict[str, Any]) -> None:
    out = data_dir / OUT_REPORT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


def _print(report: dict[str, Any]) -> None:
    s = report["summary"]
    print(f"run {report['run_id']}  model {report['model_version']}  configuration v{report['config_version']}  "
          f"analysed {report['generated_at']}")
    print(f"roads: {report['roads_in_database']} in database, {report['roads_eligible']} eligible, "
          f"{report['roads_not_eligible']} not eligible (class); rivers: {report['rivers']}")
    print(f"terrain samples: {report['terrain_samples_total']} (every {report['terrain_sample_step_m']} m)")
    print(f"scored: {s['segments_scored']}; without index: {s['segments_without_index']}; flagged: {s['segments_flagged']}")
    for tier, v in s["by_tier"].items():
        print(f"    {tier:7} {v['segments']:6d} segments  {v['length_km']:8.1f} km")
    if "index" in s:
        print(f"    index min {s['index']['min']}, median {s['index']['median']}, mean {s['index']['mean']}, max {s['index']['max']}")
    print(f"    flags: {s['flags'] or 'none'}")
    print(f"stored: {report['results_written']} written, {report['results_removed_not_eligible']} removed (no longer eligible)")
    print(f"\nNOTE: {ivi.INTERPRETATION_NOTE}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.score_roads", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    p.add_argument("--all-classes", action="store_true", help="score every highway class, not only the vehicular network")
    p.add_argument("--step", type=float, help="distance between terrain samples in metres (default 30)")
    p.add_argument("--new-config", action="store_true", help="store the code defaults as a new active configuration first")
    p.add_argument("--verify-only", action="store_true", help="check the stored results and change nothing")
    args = p.parse_args(argv)
    try:
        from app.db import get_engine

        engine = get_engine()
    except Exception as exc:  # noqa: BLE001 - configuration problems; never echo connection details
        print(f"FAILED: database is not configured ({type(exc).__name__}); check .env", file=sys.stderr)
        return 1
    try:
        if args.verify_only:
            with engine.connect() as conn:
                active = load_active_config(conn)
                if active is None:
                    raise ScoringError("no active scoring configuration; nothing has been scored yet")
                check = verify_results(conn, active[0])
            print("PASS" if check["ok"] else "FAIL")
            for k, v in check["stats"].items():
                print(f"    {k}: {v}")
            for e in check["errors"]:
                print(f"    ERROR {e}", file=sys.stderr)
            return 0 if check["ok"] else 1
        report = run(args.data_dir, engine, all_classes=args.all_classes, step_m=args.step, new_config=args.new_config)
    except AcquisitionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"FAILED: database error ({type(exc).__name__})", file=sys.stderr)
        return 1
    _print(report)
    print(f"\nreport written to {args.data_dir / OUT_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
