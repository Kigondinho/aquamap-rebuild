"""scripts/init_env.py: a .env with fresh secrets, private to its owner, never overwritten by accident."""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("init_env", ROOT / "scripts" / "init_env.py")
init_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init_env)  # type: ignore[union-attr]


def read(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


def test_creates_a_private_env_with_random_secrets(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    assert init_env.main(["--env-file", str(target)]) == 0
    values = read(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert len(values["SECRET_KEY"]) >= 32 and not values["SECRET_KEY"].startswith("change-me")
    assert len(values["POSTGRES_PASSWORD"]) >= 24 and not values["POSTGRES_PASSWORD"].startswith("change-me")
    assert values["APP_ENV"] == "development" and values["POSTGRES_PORT"] == "5433" and values["APP_PORT"] == "8000"
    assert values["HOST_UID"].isdigit() and values["HOST_GID"].isdigit()


def test_every_run_gets_different_secrets(tmp_path: Path) -> None:
    a, b = tmp_path / "a.env", tmp_path / "b.env"
    init_env.main(["--env-file", str(a)])
    init_env.main(["--env-file", str(b)])
    assert read(a)["SECRET_KEY"] != read(b)["SECRET_KEY"] and read(a)["POSTGRES_PASSWORD"] != read(b)["POSTGRES_PASSWORD"]


def test_ports_can_be_chosen(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    init_env.main(["--env-file", str(target), "--app-port", "8088", "--db-port", "5544"])
    assert (read(target)["APP_PORT"], read(target)["POSTGRES_PORT"]) == ("8088", "5544")


def test_an_existing_env_is_kept_unless_forced(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("SECRET_KEY=keep-me\n")
    assert init_env.main(["--env-file", str(target)]) == 1
    assert target.read_text() == "SECRET_KEY=keep-me\n"
    assert init_env.main(["--env-file", str(target), "--force"]) == 0
    assert read(target)["SECRET_KEY"] != "keep-me"


def test_the_result_is_accepted_by_the_application_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.settings import Settings

    target = tmp_path / ".env"
    init_env.main(["--env-file", str(target)])
    for name in ("SECRET_KEY", "POSTGRES_PASSWORD", "POSTGRES_PORT", "POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER",
                 "APP_ENV", "EMAIL_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=target)
    assert settings.secret_key.get_secret_value() == read(target)["SECRET_KEY"]


def test_render_replaces_commented_and_plain_keys_once() -> None:
    template = "A=1\n# B=2\nA=3\n"
    assert init_env.render(template, {"A": "x", "B": "y", "C": "z"}) == "A=x\nB=y\nA=3\n\n# Added by scripts/init_env.py\nC=z\n"
