"""The repository layout matches the approved architecture (Phase 1, section 10)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "README.md", ".gitignore", ".env.example", ".dockerignore",
    "Dockerfile", "docker-compose.yml", "pytest.ini",
    "requirements.txt", "requirements-dev.txt", "requirements-pipeline.txt",
    "db/init/001_extensions.sql", "db/init/002_schema.sql", "db/init/003_indexes.sql", "db/init/004_views.sql",
    "core/__init__.py", "core/ivi.py", "core/config_defaults.py", "core/geo.py",
    "pipeline/__init__.py", "pipeline/fetch_data.py", "pipeline/prepare_dem.py",
    "pipeline/load_vectors.py", "pipeline/compute_metrics.py", "pipeline/score_roads.py",
    "pipeline/run_pipeline.py", "pipeline/errors.py", "pipeline/provenance.py", "pipeline/validate.py",
    "pipeline/process_vectors.py", "pipeline/acquire.py", "docs/phase4_data_acquisition.md", "docs/phase5_dem_terrain.md",
    "pipeline/geometry.py", "docs/phase6_ingestion.md", "docs/phase7_ivi_engine.md",
    "app/__init__.py", "app/main.py", "app/settings.py", "app/db.py", "app/db_tools.py", "app/deps.py", "app/errors.py",
    "app/routers/__init__.py", "app/routers/auth.py", "app/routers/roads.py",
    "app/routers/admin.py", "app/routers/meta.py", "app/routers/health.py", "app/routers/pages.py",
    "app/schemas/auth.py", "app/schemas/roads.py", "app/schemas/admin.py", "app/schemas/meta.py",
    "app/services/auth.py", "app/services/passwords.py", "app/services/sessions.py",
    "app/services/email.py", "app/services/roads_query.py", "app/services/export.py",
    "app/services/admin.py",
    "static/index.html", "static/login.html", "static/register.html",
    "static/verify-2fa.html", "static/admin.html", "static/css/style.css",
    "static/js/app.js", "static/js/api.js", "static/js/map.js", "static/js/filters.js",
    "static/js/popup.js", "static/js/summary.js", "static/js/auth.js", "static/js/risk-config.js",
    "static/vendor/leaflet/leaflet.js", "static/vendor/leaflet/leaflet.css",
]


@pytest.mark.parametrize("relative", REQUIRED_FILES)
def test_required_file_exists(relative: str) -> None:
    assert (ROOT / relative).is_file(), f"missing {relative}"


@pytest.mark.parametrize("relative", ["data/raw/dem", "data/raw/osm", "data/raw/rivers", "data/raw/boundary", "data/cache", "data/processed"])
def test_data_directories_exist(relative: str) -> None:
    assert (ROOT / relative).is_dir()


def test_env_example_has_no_real_secrets() -> None:
    text = (ROOT / ".env.example").read_text()
    assert "SECRET_KEY=change-me" in text
    assert "POSTGRES_PASSWORD=change-me" in text


def test_real_env_file_is_gitignored() -> None:
    ignore = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignore
