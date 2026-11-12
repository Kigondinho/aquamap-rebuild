"""Constraints, relationships and cascades. Every test runs in a rolled-back transaction."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import Connection, text

from tests.db.conftest import ORIGIN_SQL, expect_db_error

pytestmark = pytest.mark.db

USER_SQL = "INSERT INTO users (email, password_hash, role) VALUES (:email, 'hash', :role)"


# --- users ------------------------------------------------------------------
def test_user_inserts_and_defaults(conn: Connection) -> None:
    row = conn.execute(
        text("INSERT INTO users (email, password_hash) VALUES ('a@example.com', 'h') RETURNING role, is_active, user_id")
    ).one()
    assert row.role == "user" and row.is_active is True and row.user_id is not None


def test_duplicate_email_rejected(conn: Connection) -> None:
    conn.execute(text(USER_SQL), {"email": "dup@example.com", "role": "user"})
    expect_db_error(conn, USER_SQL, {"email": "dup@example.com", "role": "user"})


def test_uppercase_email_rejected(conn: Connection) -> None:
    expect_db_error(conn, USER_SQL, {"email": "Upper@Example.com", "role": "user"})


def test_invalid_role_rejected(conn: Connection) -> None:
    expect_db_error(conn, USER_SQL, {"email": "r@example.com", "role": "superuser"})


# --- layers, config, runs ---------------------------------------------------
def test_invalid_layer_type_rejected(conn: Connection) -> None:
    expect_db_error(conn, "INSERT INTO spatial_layers (layer_name, layer_type) VALUES ('x', 'photos')")


def test_only_one_active_config(conn: Connection, insert_config: Callable[..., int]) -> None:
    conn.execute(text("UPDATE scoring_config SET is_active = FALSE"))  # the database may already hold a real one
    insert_config(active=True)
    expect_db_error(
        conn,
        "INSERT INTO scoring_config (weights, breakpoints, surface_map, tier_thresholds, is_active) "
        "VALUES ('{}', '{}', '{}', '{}', TRUE)",
    )
    insert_config(active=False)  # inactive ones are unlimited


def test_pipeline_run_cannot_finish_before_it_starts(conn: Connection) -> None:
    expect_db_error(
        conn,
        "INSERT INTO pipeline_runs (started_at, finished_at) VALUES (now(), now() - interval '1 hour')",
    )


def test_pipeline_run_status_validated(conn: Connection) -> None:
    expect_db_error(conn, "INSERT INTO pipeline_runs (status) VALUES ('paused')")


# --- geometry rules ----------------------------------------------------------
def test_geometry_in_wrong_srid_rejected(conn: Connection) -> None:
    expect_db_error(
        conn,
        "INSERT INTO road_segments (highway_type, geom) VALUES "
        "('residential', ST_SetSRID(ST_MakeLine(ST_MakePoint(36.82, -1.29), ST_MakePoint(36.83, -1.29)), 4326))",
    )


def test_wrong_geometry_type_rejected(conn: Connection) -> None:
    expect_db_error(conn, f"INSERT INTO road_segments (highway_type, geom) VALUES ('residential', {ORIGIN_SQL})")


def test_degenerate_zero_length_line_rejected(conn: Connection) -> None:
    expect_db_error(
        conn,
        f"INSERT INTO road_segments (highway_type, geom) VALUES ('residential', ST_MakeLine({ORIGIN_SQL}, {ORIGIN_SQL}))",
    )


def test_null_geometry_rejected(conn: Connection) -> None:
    expect_db_error(conn, "INSERT INTO road_segments (highway_type, geom) VALUES ('residential', NULL)")


def test_blank_highway_type_rejected(conn: Connection) -> None:
    expect_db_error(
        conn,
        f"INSERT INTO road_segments (highway_type, geom) VALUES ('  ', "
        f"ST_MakeLine({ORIGIN_SQL}, ST_Translate({ORIGIN_SQL}, 0, 100)))",
    )


def test_generated_length_cannot_be_written(conn: Connection) -> None:
    expect_db_error(
        conn,
        f"INSERT INTO road_segments (highway_type, length_m, geom) VALUES ('residential', 5, "
        f"ST_MakeLine({ORIGIN_SQL}, ST_Translate({ORIGIN_SQL}, 0, 100)))",
    )


# --- vulnerability results ---------------------------------------------------
INSERT_RESULT = (
    "INSERT INTO road_vulnerability (road_id, config_version, vulnerability_index, risk_category, "
    "score_elevation, slope_pct) VALUES (:road, :cfg, :ivi, :tier, :score, :slope)"
)


def _result(road: int, cfg: int, ivi=3.5, tier="High", score=3.0, slope=3.0) -> dict:
    return {"road": road, "cfg": cfg, "ivi": ivi, "tier": tier, "score": score, "slope": slope}


def test_valid_result_stored_and_linked(
    conn: Connection, insert_road: Callable[..., int], insert_config: Callable[..., int]
) -> None:
    road, cfg = insert_road(), insert_config()
    conn.execute(text(INSERT_RESULT), _result(road, cfg))
    row = conn.execute(
        text("SELECT risk_category, data_flags FROM road_vulnerability WHERE road_id = :r"), {"r": road}
    ).one()
    assert row.risk_category == "High" and row.data_flags == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"ivi": 5.001},  # IVI above 5
        {"ivi": 0.999},  # IVI below 1
        {"tier": "Critical"},  # unknown tier name
        {"tier": None},  # IVI without tier
        {"ivi": None},  # tier without IVI
        {"score": 5.5},  # factor score above 5
        {"score": 0.5},  # factor score below 1
        {"slope": -1.0},  # negative slope
    ],
)
def test_invalid_result_values_rejected(
    conn: Connection, insert_road: Callable[..., int], insert_config: Callable[..., int], overrides: dict
) -> None:
    road, cfg = insert_road(), insert_config()
    expect_db_error(conn, INSERT_RESULT, _result(road, cfg) | overrides)


def test_result_requires_existing_road_and_config(
    conn: Connection, insert_road: Callable[..., int], insert_config: Callable[..., int]
) -> None:
    road, cfg = insert_road(), insert_config()
    expect_db_error(conn, INSERT_RESULT, _result(-1, cfg))
    expect_db_error(conn, INSERT_RESULT, _result(road, -1))


def test_one_current_result_per_road(
    conn: Connection, insert_road: Callable[..., int], insert_config: Callable[..., int]
) -> None:
    road, cfg = insert_road(), insert_config()
    conn.execute(text(INSERT_RESULT), _result(road, cfg))
    expect_db_error(conn, INSERT_RESULT, _result(road, cfg))


def test_deleting_a_road_deletes_its_result(
    conn: Connection, insert_road: Callable[..., int], insert_config: Callable[..., int]
) -> None:
    road, cfg = insert_road(), insert_config()
    conn.execute(text(INSERT_RESULT), _result(road, cfg))
    conn.execute(text("DELETE FROM road_segments WHERE road_id = :r"), {"r": road})
    assert conn.execute(text("SELECT count(*) FROM road_vulnerability WHERE road_id = :r"), {"r": road}).scalar_one() == 0


def test_deleting_a_run_keeps_the_result(
    conn: Connection, insert_road: Callable[..., int], insert_config: Callable[..., int]
) -> None:
    road, cfg = insert_road(), insert_config()
    run = conn.execute(text("INSERT INTO pipeline_runs (config_version) VALUES (:c) RETURNING run_id"), {"c": cfg}).scalar_one()
    conn.execute(
        text("INSERT INTO road_vulnerability (road_id, config_version, run_id) VALUES (:r, :c, :run)"),
        {"r": road, "c": cfg, "run": run},
    )
    conn.execute(text("DELETE FROM pipeline_runs WHERE run_id = :run"), {"run": run})
    kept = conn.execute(text("SELECT run_id FROM road_vulnerability WHERE road_id = :r"), {"r": road}).one()
    assert kept.run_id is None


def test_config_in_use_cannot_be_deleted(
    conn: Connection, insert_road: Callable[..., int], insert_config: Callable[..., int]
) -> None:
    road, cfg = insert_road(), insert_config()
    conn.execute(text(INSERT_RESULT), _result(road, cfg))
    expect_db_error(conn, "DELETE FROM scoring_config WHERE config_version = :c", {"c": cfg})


def test_deleting_a_user_deletes_their_codes(conn: Connection) -> None:
    user = conn.execute(text(USER_SQL + " RETURNING user_id"), {"email": "otp@example.com", "role": "user"}).scalar_one()
    conn.execute(
        text("INSERT INTO auth_otps (user_id, otp_hash, expires_at) VALUES (:u, 'h', now() + interval '5 min')"),
        {"u": user},
    )
    conn.execute(text("DELETE FROM users WHERE user_id = :u"), {"u": user})
    assert conn.execute(text("SELECT count(*) FROM auth_otps WHERE user_id = :u"), {"u": user}).scalar_one() == 0
