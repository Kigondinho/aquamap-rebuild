"""Command line for data acquisition: fetch, process and validate.

    python -m pipeline.acquire all                 # boundary, roads, rivers, DEM, process, validate
    python -m pipeline.acquire fetch roads         # one step
    python -m pipeline.acquire validate            # re-check what is on disk
    python -m pipeline.acquire all --force         # download again even if files exist

Exit status: 0 everything fetched and valid, 1 any failure or failed check. Failures are listed at the
end; one failed step does not hide the others.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from pipeline import fetch_data as fd
from pipeline.errors import AcquisitionError
from pipeline.provenance import utc_now_iso, verify_checksums
from pipeline.validate import Report

STEPS = ("boundary", "roads", "rivers", "dem")


def _read_gdf(path: Path):  # noqa: ANN202
    import geopandas as gpd

    return gpd.read_file(path)


def run_fetch(step: str, args: argparse.Namespace, raw: Path, cache: Path) -> Path:
    if step == "boundary":
        return fd.fetch_boundary(raw, cache, boundary_file=args.boundary_file, force=args.force)
    if step == "roads":
        return fd.fetch_roads(raw, cache, force=args.force)
    if step == "rivers":
        return fd.fetch_rivers(raw, cache, force=args.force)
    if step == "dem":
        return fd.fetch_dem(raw, boundary_path=raw / fd.RAW_BOUNDARY, dem_file=args.dem_file,
                            dem_source=args.dem_source, force=args.force)
    raise ValueError(step)


def validate_all(data_dir: Path) -> list[Report]:
    """Validate raw and processed datasets that exist; absent ones are reported as errors."""
    from core.config_defaults import STORAGE_SRID
    from pipeline import validate as v

    raw, proc = data_dir / "raw", data_dir / "processed"
    reports: list[Report] = []
    boundary = None
    bpath = raw / fd.RAW_BOUNDARY
    if bpath.exists():
        boundary = _read_gdf(bpath)
        reports.append(v.validate_boundary(boundary, str(bpath)))
    else:
        r = Report("boundary", str(bpath))
        r.add("error", "completeness", "boundary file is missing")
        reports.append(r)
    layers = [("roads", raw / fd.RAW_ROADS, 4326, "raw"), ("rivers", raw / fd.RAW_RIVERS, 4326, "raw"),
              ("roads", proc / "roads.gpkg", STORAGE_SRID, "processed"),
              ("rivers", proc / "rivers.gpkg", STORAGE_SRID, "processed")]
    for kind, path, epsg, stage in layers:
        if not path.exists():
            r = Report(f"{kind} ({stage})", str(path))
            r.add("error", "completeness", "file is missing" if stage == "raw" else "not produced yet (run 'process')")
            reports.append(r)
            continue
        r = v.validate_vector(_read_gdf(path), kind, path=str(path), expected_epsg=epsg, boundary=boundary)
        r.dataset = f"{kind} ({stage})"
        for problem in verify_checksums(path):
            r.add("warning", "provenance", problem)
        reports.append(r)
    dem = raw / fd.DEM_MOSAIC
    if dem.exists():
        r = v.validate_dem(dem, boundary)
        for problem in verify_checksums(dem):
            r.add("warning", "provenance", problem)
    else:
        r = Report("dem", str(dem))
        r.add("error", "completeness", "DEM file is missing")
    reports.append(r)
    return reports


def print_reports(reports: list[Report]) -> None:
    for r in reports:
        status = "PASS" if r.ok else "FAIL"
        print(f"\n[{status}] {r.dataset}  ({r.path})")
        for key in ("feature_count", "crs", "geometry_types", "area_km2", "grid_cells_with_road_pct",
                    "width", "height", "approx_cell_m", "min_m", "max_m", "void_pct"):
            if key in r.stats:
                print(f"    {key}: {r.stats[key]}")
        for i in r.issues:
            if i.level != "info":
                print(f"    {i.level.upper():7} [{i.check}] {i.message}")


def save_report(reports: list[Report], data_dir: Path) -> Path:
    out = data_dir / "processed" / "validation_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": utc_now_iso(), "all_ok": all(r.ok for r in reports),
                               "reports": [r.to_dict() for r in reports]}, indent=2) + "\n", encoding="utf-8")
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m pipeline.acquire", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    p.add_argument("--force", action="store_true", help="download again even if the raw file exists")
    p.add_argument("--boundary-file", type=Path, help="use this polygon file instead of the Nominatim lookup")
    p.add_argument("--dem-file", type=Path, help="adopt a GeoTIFF DEM you downloaded yourself")
    p.add_argument("--dem-source", help="who published --dem-file and under which licence (required with it)")
    sub = p.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="download raw data")
    f.add_argument("what", choices=(*STEPS, "all"), nargs="?", default="all")
    sub.add_parser("process", help="raw vectors -> processed EPSG:32737 layers")
    sub.add_parser("validate", help="check everything on disk and write validation_report.json")
    sub.add_parser("all", help="fetch, process, validate")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir: Path = args.data_dir
    raw, proc, cache = data_dir / "raw", data_dir / "processed", data_dir / "cache"
    failures: list[str] = []

    def attempt(label: str, func: Callable[[], object]) -> bool:
        print(f"==> {label}")
        try:
            result = func()
        except AcquisitionError as exc:
            failures.append(f"{label}: {exc}")
            print(f"    FAILED: {exc}")
            return False
        except Exception as exc:  # noqa: BLE001 - unexpected; still report, never traceback-and-hide
            failures.append(f"{label}: unexpected {type(exc).__name__}: {exc}")
            print(f"    FAILED (unexpected): {type(exc).__name__}: {exc}")
            return False
        print(f"    ok: {result}")
        return True

    if args.command in ("fetch", "all"):
        wanted = STEPS if args.command == "all" or args.what == "all" else (args.what,)
        boundary_ok = True
        for step in wanted:
            if step in ("roads", "rivers") and not boundary_ok:
                failures.append(f"fetch {step}: skipped because the boundary step failed")
                print(f"==> fetch {step}\n    SKIPPED: no county boundary")
                continue
            ok = attempt(f"fetch {step}", lambda s=step: run_fetch(s, args, raw, cache))
            if step == "boundary":
                boundary_ok = ok
    if args.command in ("process", "all"):
        from pipeline.process_vectors import process_vector

        for kind, rel in (("roads", fd.RAW_ROADS), ("rivers", fd.RAW_RIVERS)):
            attempt(f"process {kind}", lambda k=kind, r=rel: process_vector(raw / r, k, proc, data_dir))
    if args.command in ("validate", "all"):
        reports = validate_all(data_dir)
        print_reports(reports)
        print(f"\nreport written to {save_report(reports, data_dir)}")
        failures += [f"validation {r.dataset}: {i.message}" for r in reports for i in r.errors]
    if failures:
        print("\nFAILURES:", *[f"  - {m}" for m in failures], sep="\n", file=sys.stderr)
        return 1
    print("\nAll requested steps succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
