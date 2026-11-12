"""Database initialisation and diagnostics.

Command line (run from the project root, with the same environment as the app):

    python -m app.db_tools init     # apply db/init/*.sql in order, in one transaction
    python -m app.db_tools check    # connectivity, PostGIS, tables, SRIDs, GiST indexes

The init scripts are idempotent, so ``init`` is safe to run repeatedly and also works on a
database that Docker has already initialised. Passwords are never printed.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, text

from app.db import get_engine
from core.config_defaults import STORAGE_SRID

INIT_DIR = Path(__file__).resolve().parents[1] / "db" / "init"

EXPECTED_TABLES: tuple[str, ...] = (
    "users", "auth_otps", "spatial_layers", "scoring_config", "pipeline_runs",
    "road_segments", "river_centerlines", "road_vulnerability",
)
EXPECTED_VIEWS: tuple[str, ...] = ("v_road_risk",)
# (table, column) -> (geometry type, SRID)
EXPECTED_GEOMETRY: dict[tuple[str, str], tuple[str, int]] = {
    ("road_segments", "geom"): ("LINESTRING", STORAGE_SRID),
    ("river_centerlines", "geom"): ("LINESTRING", STORAGE_SRID),
}
EXPECTED_GIST_INDEXES: tuple[str, ...] = ("road_segments_geom_gist", "river_centerlines_geom_gist")


def init_script_paths() -> list[Path]:
    """The SQL init scripts, in execution (name) order."""
    return sorted(INIT_DIR.glob("*.sql"))


def apply_init_scripts(engine: Engine | None = None) -> list[str]:
    """Run every init script inside a single transaction. Returns the file names applied."""
    engine = engine or get_engine()
    applied: list[str] = []
    with engine.begin() as conn:  # commits on success, rolls everything back on error
        for path in init_script_paths():
            conn.exec_driver_sql(path.read_text(encoding="utf-8"))
            applied.append(path.name)
    return applied


@dataclass
class Diagnostics:
    server_version: str = ""
    postgis_version: str = ""
    tables_present: list[str] = field(default_factory=list)
    views_present: list[str] = field(default_factory=list)
    geometry_columns: dict[tuple[str, str], tuple[str, int]] = field(default_factory=dict)
    gist_indexes: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        issues: list[str] = []
        if not self.postgis_version:
            issues.append("PostGIS extension is not installed")
        issues += [f"missing table: {t}" for t in EXPECTED_TABLES if t not in self.tables_present]
        issues += [f"missing view: {v}" for v in EXPECTED_VIEWS if v not in self.views_present]
        for key, (geom_type, srid) in EXPECTED_GEOMETRY.items():
            actual = self.geometry_columns.get(key)
            if actual is None:
                issues.append(f"missing geometry column: {key[0]}.{key[1]}")
            elif actual != (geom_type, srid):
                issues.append(f"{key[0]}.{key[1]} is {actual[0]}/{actual[1]}, expected {geom_type}/{srid}")
        issues += [f"missing GiST index: {i}" for i in EXPECTED_GIST_INDEXES if i not in self.gist_indexes]
        return issues

    @property
    def healthy(self) -> bool:
        return not self.problems


def run_diagnostics(engine: Engine | None = None) -> Diagnostics:
    """Inspect the live database. Raises if it cannot connect."""
    engine = engine or get_engine()
    result = Diagnostics()
    with engine.connect() as conn:
        result.server_version = conn.execute(text("SHOW server_version")).scalar_one()
        result.postgis_version = (
            conn.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'postgis'")).scalar() or ""
        )
        result.tables_present = [
            r[0] for r in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
            )
        ]
        result.views_present = [
            r[0] for r in conn.execute(
                text("SELECT viewname FROM pg_views WHERE schemaname = current_schema()")
            )
        ]
        if result.postgis_version:  # the geometry_columns view only exists with PostGIS
            result.geometry_columns = {
                (r[0], r[1]): (r[2], r[3])
                for r in conn.execute(
                    text(
                        "SELECT f_table_name, f_geometry_column, type, srid FROM geometry_columns "
                        "WHERE f_table_schema = current_schema()"
                    )
                )
                if r[0] not in EXPECTED_VIEWS
            }
        result.gist_indexes = [
            r[0] for r in conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema() AND indexdef ILIKE '%USING gist%'"
                )
            )
        ]
    return result


def _print_diagnostics(d: Diagnostics) -> None:
    print(f"PostgreSQL {d.server_version}, PostGIS {d.postgis_version or 'NOT INSTALLED'}")
    print(f"Tables  : {', '.join(sorted(d.tables_present)) or '(none)'}")
    for (table, column), (geom_type, srid) in sorted(d.geometry_columns.items()):
        print(f"Geometry: {table}.{column} = {geom_type}, EPSG:{srid}")
    print(f"GiST    : {', '.join(sorted(d.gist_indexes)) or '(none)'}")
    if d.healthy:
        print("OK: database is initialised and consistent.")
    else:
        print("PROBLEMS:")
        for issue in d.problems:
            print(f"  - {issue}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.db_tools", description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["init", "check"])
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            for name in apply_init_scripts():
                print(f"applied {name}")
        diagnostics = run_diagnostics()
    except Exception as exc:  # report cleanly; never echo connection details
        print(f"Database error ({exc.__class__.__name__}). Is the database running and configured in .env?",
              file=sys.stderr)
        return 1

    _print_diagnostics(diagnostics)
    return 0 if diagnostics.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
