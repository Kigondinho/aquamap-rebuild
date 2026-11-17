"""The rule that keeps tests away from real data: only a database whose name ends in ``_test`` is accepted."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import testdb


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://u:p@localhost:5434/aquamap_db",          # the development database
    "postgresql+psycopg://u:p@db.example.com:5432/aquamap",        # a production-looking name
    "postgresql+psycopg://u:p@localhost/aquamap_test_backup",      # "_test" in the middle is not enough
    "postgresql+psycopg://u:p@localhost/test_aquamap",
    "postgresql+psycopg://u:p@localhost/_test",                    # nothing but the suffix
    "postgresql+psycopg://u:p@localhost/postgres",
    "postgresql+psycopg://u:p@localhost/",
    "sqlite:///aquamap_test",                                      # right name, wrong kind of database
])
def test_anything_but_a_dedicated_test_database_is_refused(url: str) -> None:
    with pytest.raises(testdb.UnsafeTestDatabase):
        testdb.ensure_safe(url)


@pytest.mark.parametrize("name", ["aquamap_db_test", "aquamap_test", "x_test"])
def test_a_test_database_is_accepted(name: str) -> None:
    assert testdb.ensure_safe(f"postgresql+psycopg://u:p@localhost:5434/{name}").database == name


def test_the_test_database_is_derived_from_dotenv_with_the_suffix(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("POSTGRES_HOST=dbhost\nPOSTGRES_PORT=6543\nPOSTGRES_DB=aquamap_db\nPOSTGRES_USER=someone\n"
                   "POSTGRES_PASSWORD=secret-value\n")
    url = testdb.url_from_dotenv(env)
    assert (url.host, url.port, url.database, url.username) == ("dbhost", 6543, "aquamap_db_test", "someone")
    assert testdb.ensure_safe(url) == url


def test_a_missing_dotenv_or_password_is_reported(tmp_path: Path) -> None:
    with pytest.raises(testdb.UnsafeTestDatabase, match="not found"):
        testdb.url_from_dotenv(tmp_path / "missing.env")
    env = tmp_path / ".env"
    env.write_text("POSTGRES_DB=aquamap_db\n")
    with pytest.raises(testdb.UnsafeTestDatabase, match="POSTGRES_PASSWORD"):
        testdb.url_from_dotenv(env)


def test_an_explicit_url_is_checked_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(testdb.ENV_VAR, "postgresql+psycopg://u:p@localhost:5434/aquamap_db")
    with pytest.raises(testdb.UnsafeTestDatabase):
        testdb.resolve(use_dotenv=True)
    monkeypatch.delenv(testdb.ENV_VAR)
    assert testdb.resolve(use_dotenv=False) is None   # nothing requested: database tests are skipped


def test_creating_a_database_refuses_an_unsafe_name_before_connecting() -> None:
    # Port 1 is closed: if the name check did not come first, this would fail with a connection error instead.
    with pytest.raises(testdb.UnsafeTestDatabase):
        testdb.create_if_missing("postgresql+psycopg://u:p@127.0.0.1:1/aquamap_db")
