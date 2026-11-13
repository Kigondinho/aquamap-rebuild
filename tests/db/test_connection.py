"""Connectivity, PostGIS availability, transactions and the init/diagnostics tooling."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, text

from app import db
from app.db_tools import apply_init_scripts, run_diagnostics

pytestmark = pytest.mark.db


@pytest.fixture
def app_engine(database_url: str, db_engine: Engine) -> Iterator[Engine]:
    """Point the application's own engine (app.db) at the test database."""
    engine = db.init_engine(database_url)
    yield engine
    db.dispose_engine()


def test_can_connect(conn: Connection) -> None:
    assert conn.execute(text("SELECT 1")).scalar_one() == 1


def test_postgis_extension_installed(conn: Connection) -> None:
    version = conn.execute(text("SELECT postgis_version()")).scalar_one()
    assert version.startswith("3.")
    extensions = {r[0] for r in conn.execute(text("SELECT extname FROM pg_extension"))}
    assert "postgis" in extensions


def test_projected_crs_32737_is_known_to_postgis(conn: Connection) -> None:
    name = conn.execute(text("SELECT srtext FROM spatial_ref_sys WHERE srid = 32737")).scalar_one()
    assert "UTM zone 37S" in name


def test_app_check_connection(app_engine: Engine) -> None:
    assert db.check_connection() is True


def test_session_scope_commits_on_success(app_engine: Engine) -> None:
    name = f"pytest-commit-{uuid.uuid4()}"
    try:
        with db.session_scope() as session:
            session.execute(
                text("INSERT INTO spatial_layers (layer_name, layer_type) VALUES (:n, 'roads')"), {"n": name}
            )
        with app_engine.connect() as other:
            count = other.execute(
                text("SELECT count(*) FROM spatial_layers WHERE layer_name = :n"), {"n": name}
            ).scalar_one()
        assert count == 1
    finally:
        with app_engine.begin() as cleanup:
            cleanup.execute(text("DELETE FROM spatial_layers WHERE layer_name = :n"), {"n": name})


def test_session_scope_rolls_back_on_error(app_engine: Engine) -> None:
    name = f"pytest-rollback-{uuid.uuid4()}"
    with pytest.raises(RuntimeError):
        with db.session_scope() as session:
            session.execute(
                text("INSERT INTO spatial_layers (layer_name, layer_type) VALUES (:n, 'roads')"), {"n": name}
            )
            raise RuntimeError("boom")
    with app_engine.connect() as other:
        count = other.execute(
            text("SELECT count(*) FROM spatial_layers WHERE layer_name = :n"), {"n": name}
        ).scalar_one()
    assert count == 0


def test_get_db_dependency_does_not_commit_implicitly(app_engine: Engine) -> None:
    name = f"pytest-dep-{uuid.uuid4()}"
    generator = db.get_db()
    session = next(generator)
    session.execute(text("INSERT INTO spatial_layers (layer_name, layer_type) VALUES (:n, 'roads')"), {"n": name})
    generator.close()  # request finished without commit()
    assert not session.in_transaction()
    with app_engine.connect() as other:
        count = other.execute(
            text("SELECT count(*) FROM spatial_layers WHERE layer_name = :n"), {"n": name}
        ).scalar_one()
    assert count == 0


def test_init_scripts_can_be_reapplied(db_engine: Engine) -> None:
    first = apply_init_scripts(db_engine)
    second = apply_init_scripts(db_engine)
    assert first == second == [
        "001_extensions.sql", "002_schema.sql", "003_indexes.sql", "004_views.sql",
        "005_classification.sql",
    ]


def test_diagnostics_report_a_healthy_database(db_engine: Engine) -> None:
    diagnostics = run_diagnostics(db_engine)
    assert diagnostics.problems == []
    assert diagnostics.healthy
    assert diagnostics.postgis_version.startswith("3.")
