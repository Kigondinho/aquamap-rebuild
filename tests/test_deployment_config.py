"""Docker, Compose, CI and repository hygiene: the settings that keep the deployment secure and reproducible.

These read the files; ``docker compose config`` and the CI "docker" job check that they also work.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML is in requirements-dev.txt")

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (ROOT / "Dockerfile").read_text(encoding="utf-8")


# ---------------------------------------------------------------- Compose
def test_services(compose: dict) -> None:
    services = compose["services"]
    assert set(services) == {"db", "app", "pipeline"}
    assert services["pipeline"]["profiles"] == ["pipeline"]                    # not started by "up"
    for name in ("app", "pipeline"):
        assert services[name]["depends_on"]["db"]["condition"] == "service_healthy"
        assert services[name]["env_file"] == ".env"
        assert services[name]["environment"]["POSTGRES_HOST"] == "db"


def test_published_ports_listen_on_this_machine_only(compose: dict) -> None:
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} to every network interface"


def test_database_image_is_pinned_and_has_a_real_healthcheck(compose: dict) -> None:
    db = compose["services"]["db"]
    assert DIGEST.search(db["image"]), "pin the PostGIS image by digest"
    check = " ".join(db["healthcheck"]["test"])
    assert "pg_isready" in check and "-h 127.0.0.1" in check     # TCP, so first-time setup is not mistaken for ready


def test_secrets_come_only_from_the_environment(compose: dict) -> None:
    db_env = compose["services"]["db"]["environment"]
    assert db_env["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")   # refuses to start without it
    for name, service in compose["services"].items():
        for key, value in (service.get("environment") or {}).items():
            if re.search(r"PASSWORD|SECRET|KEY|TOKEN", key):
                assert str(value).startswith("${"), f"{name}.{key} is written into docker-compose.yml"


def test_no_fixed_container_names_and_no_init_bind_mount(compose: dict) -> None:
    for name, service in compose["services"].items():
        assert "container_name" not in service, f"{name}: a fixed name stops two checkouts from running side by side"
        if "build" in service:
            assert "image" not in service, f"{name}: a fixed image name lets two checkouts overwrite each other's image"
    db_volumes = compose["services"]["db"].get("volumes", [])
    assert not any("docker-entrypoint-initdb.d" in str(v) for v in db_volumes), \
        "the schema is created by the app's entrypoint, not from a host folder whose permissions may block it"


def test_pipeline_writes_data_as_the_checkout_owner(compose: dict) -> None:
    pipeline = compose["services"]["pipeline"]
    assert pipeline["user"] == "${HOST_UID:-1000}:${HOST_GID:-1000}"
    assert pipeline["volumes"] == ["./data:/app/data"]
    assert pipeline["command"] == ["python", "-m", "pipeline.run_pipeline"]


# ---------------------------------------------------------------- Dockerfile and image contents
def test_base_image_is_pinned(dockerfile: str) -> None:
    base = re.search(r"^FROM\s+(\S+)", dockerfile, re.M).group(1)
    assert base.startswith("python:3.12") and DIGEST.search(base)


def test_container_runs_unprivileged_with_healthcheck_and_entrypoint(dockerfile: str) -> None:
    assert re.search(r"^USER aquamap$", dockerfile, re.M)
    assert dockerfile.index("USER aquamap") < dockerfile.index("CMD [")
    assert "HEALTHCHECK" in dockerfile and "/api/v1/health" in dockerfile
    assert 'ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]' in dockerfile


def test_image_copies_only_what_the_application_needs(dockerfile: str) -> None:
    assert not re.search(r"^COPY\s+(--\S+\s+)*\.\s+\.", dockerfile, re.M), "COPY . . would pull in everything"
    copied = set(re.findall(r"^COPY\s+(?:--\S+\s+)*(\S+)\s+\S+$", dockerfile, re.M))
    assert copied == {"app", "core", "pipeline", "db", "static", "docker/entrypoint.sh"} | {"requirements.txt"} \
        or copied == {"app", "core", "pipeline", "db", "static", "docker/entrypoint.sh"}
    assert not re.search(r"^(ENV|ARG)\s+.*(SECRET|PASSWORD)", dockerfile, re.M | re.I)


def test_dockerignore_keeps_secrets_and_bulk_out_of_the_build_context() -> None:
    entries = {line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines() if line.strip() and not line.startswith("#")}
    assert {".env", ".env.*", ".git", ".venv", "data", "tests", ".claude"} <= entries


def test_entrypoint_initialises_then_hands_over() -> None:
    script = (ROOT / "docker/entrypoint.sh").read_text()
    assert script.startswith("#!/bin/sh") and "set -eu" in script
    assert "python -m app.db_tools init" in script and script.rstrip().endswith('exec "$@"')


# ---------------------------------------------------------------- CI
def test_ci_runs_every_test_and_fails_on_skips(workflow: dict) -> None:
    triggers = workflow.get("on", workflow.get(True))   # YAML 1.1 reads a bare "on" key as True
    assert "pull_request" in triggers and triggers["push"]["branches"] == ["main"]
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["tests"]["steps"]
    commands = " ".join(step.get("run", "") for step in steps)
    assert "pytest --test-db --require-all --report=" in commands   # one word: see the root conftest.py
    assert "openapi_export --check" in commands
    assert "requirements-dev.txt -r requirements-pipeline.txt" in commands
    url = workflow["jobs"]["tests"]["env"]["AQUAMAP_TEST_DATABASE_URL"]
    assert url.split("/")[-1].endswith("_test")


def test_ci_database_image_matches_compose(workflow: dict, compose: dict) -> None:
    assert workflow["jobs"]["tests"]["services"]["postgis"]["image"] == compose["services"]["db"]["image"]


def test_ci_docker_job_starts_the_stack_from_a_clean_checkout(workflow: dict) -> None:
    commands = " ".join(step.get("run", "") for step in workflow["jobs"]["docker"]["steps"])
    for expected in ("scripts/init_env.py", "docker compose config", "docker compose up -d --wait",
                     "/api/v1/health", "db_tools check", "--password-stdin", "docker compose down -v"):
        assert expected in commands, expected


def test_ci_uses_no_stored_secrets() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    assert not re.search(r"\$\{\{\s*secrets\.", text), "CI must not depend on repository secrets"
    assert "POSTGRES_PASSWORD:" not in text   # the CI database trusts local connections; the docker job makes its own .env


# ---------------------------------------------------------------- repository hygiene
FORBIDDEN = re.compile(
    r"(^|/)(\.env(\.(?!example$)[^/]+)?$|\.venv/|venv/|\.claude/|__pycache__/|\.pytest_cache/|node_modules/)"
    r"|\.(log|tif|tiff|gpkg|shp|dump|sqlite3?|pem|key|p12)$"
    r"|^data/(raw|processed|cache)/(?!.*(\.gitkeep|\.provenance\.json|validation_report\.json)$)")


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="not a git checkout")
def test_no_secrets_or_local_files_would_be_committed() -> None:
    """Every file git tracks, plus every new file that `git add -A` would pick up."""
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.splitlines()
    assert ".env.example" in listed and "docker-compose.yml" in listed
    offenders = [path for path in listed if FORBIDDEN.search(path)]
    assert not offenders, offenders


@pytest.mark.parametrize("path", [".env", ".env.local", ".venv/bin/python", "a/.claude/launch.json", "data/raw/dem/x.tif",
                                  "data/processed/roads.gpkg", "server.log", "id.pem"])
def test_hygiene_rule_catches_local_files(path: str) -> None:
    assert FORBIDDEN.search(path)


@pytest.mark.parametrize("path", [".env.example", "data/raw/dem/.gitkeep", "data/processed/roads.gpkg.provenance.json",
                                  "data/processed/validation_report.json", "app/main.py"])
def test_hygiene_rule_allows_project_files(path: str) -> None:
    assert not FORBIDDEN.search(path)
