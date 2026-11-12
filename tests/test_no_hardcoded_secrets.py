"""Database passwords and other secrets must come from the environment, never from source."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED_SUFFIXES = {".py", ".yml", ".yaml", ".sql", ".md", ".sh", ".example", ".ini", ".txt", ".js", ".html"}
SKIP_PARTS = {"vendor", "__pycache__", ".git", ".venv", "venv", "tests"}

URL_WITH_PASSWORD = re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^:\s/@]+:([^@\s]+)@")
PLACEHOLDERS = {"PASSWORD", "<password>", "<password from .env>"}


def _source_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or SKIP_PARTS & set(path.relative_to(ROOT).parts):
            continue
        if path.suffix in SCANNED_SUFFIXES or path.name in {"Dockerfile", ".env.example"}:
            files.append(path)
    return files


def test_no_database_url_with_a_literal_password() -> None:
    offenders = []
    for path in _source_files():
        for match in URL_WITH_PASSWORD.finditer(path.read_text(errors="ignore")):
            if match.group(1) not in PLACEHOLDERS and not match.group(1).startswith("${"):
                offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
    assert not offenders, offenders


def test_compose_reads_the_database_password_from_the_environment() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    for line in compose.splitlines():
        if "POSTGRES_PASSWORD" in line:
            assert "${POSTGRES_PASSWORD" in line, line


def test_python_sources_do_not_assign_literal_passwords() -> None:
    pattern = re.compile(r"""(?i)\b\w*password\w*\s*=\s*["'][^"']+["']""")
    offenders = [
        str(p.relative_to(ROOT)) for p in _source_files() if p.suffix == ".py" and pattern.search(p.read_text())
    ]
    assert not offenders, offenders


def test_env_example_uses_placeholders_only() -> None:
    values = dict(
        line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert values["POSTGRES_PASSWORD"].startswith("change-me")
    assert values["SECRET_KEY"].startswith("change-me")
