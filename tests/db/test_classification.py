"""The database classifies exactly as the Python engine does, and refuses rows that disagree."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace

import pytest
from sqlalchemy import Connection, text

from core import ivi
from tests.db.conftest import expect_db_error

pytestmark = pytest.mark.db

DEFAULT = ivi.ScoringConfig()
UNEVEN = replace(DEFAULT, tier_thresholds={"Medium": 1.75, "High": 2.6, "Severe": 3.333})


@pytest.mark.parametrize("config", [DEFAULT, UNEVEN], ids=["default", "uneven"])
def test_sql_and_python_agree_on_every_storable_value(conn: Connection, config: ivi.ScoringConfig) -> None:
    """All 4,001 values a NUMERIC(4,3) index can take between 1.000 and 5.000."""
    rows = conn.execute(text(
        "SELECT (g / 1000.0)::numeric(4,3) AS ivi, ivi_tier((g / 1000.0)::numeric(4,3), CAST(:t AS jsonb)) AS tier "
        "FROM generate_series(1000, 5000) g"), {"t": json.dumps(dict(config.tier_thresholds))}).all()
    assert len(rows) == 4001
    wrong = [(float(v), tier, ivi.assign_tier(float(v), config)) for v, tier in rows
             if tier != ivi.assign_tier(float(v), config)]
    assert not wrong, wrong[:5]


@pytest.mark.parametrize(("value", "tier"), [
    (1.0, "Low"), (1.999, "Low"), (2.0, "Medium"), (2.999, "Medium"), (3.0, "High"), (3.999, "High"),
    (4.0, "Severe"), (5.0, "Severe"),
])
def test_boundary_values(conn: Connection, value: float, tier: str) -> None:
    sql = "SELECT ivi_tier(CAST(:v AS numeric), CAST(:t AS jsonb))"
    assert conn.execute(text(sql), {"v": value, "t": json.dumps(dict(DEFAULT.tier_thresholds))}).scalar_one() == tier
    assert ivi.assign_tier(value, DEFAULT) == tier


def test_no_index_or_incomplete_thresholds_give_no_category(conn: Connection) -> None:
    t = json.dumps(dict(DEFAULT.tier_thresholds))
    assert conn.execute(text("SELECT ivi_tier(NULL, CAST(:t AS jsonb))"), {"t": t}).scalar_one() is None
    assert conn.execute(text("SELECT ivi_tier(1.5, '{}'::jsonb)")).scalar_one() is None
    assert conn.execute(text("SELECT ivi_tier(1.5, '{\"Medium\": 2, \"High\": 3}'::jsonb)")).scalar_one() is None


INSERT = ("INSERT INTO road_vulnerability (road_id, config_version, vulnerability_index, risk_category) "
          "VALUES (:r, :c, :i, :t)")


@pytest.mark.parametrize(("value", "tier"), [(1.999, "Low"), (2.0, "Medium"), (3.0, "High"), (4.0, "Severe"), (None, None)])
def test_consistent_rows_are_accepted(conn: Connection, insert_road: Callable[..., int],
                                      insert_config: Callable[..., int], value: float | None, tier: str | None) -> None:
    conn.execute(text(INSERT), {"r": insert_road(), "c": insert_config(), "i": value, "t": tier})


@pytest.mark.parametrize(("value", "tier"), [(2.0, "Low"), (1.999, "Medium"), (3.999, "Severe"), (4.0, "High"), (5.0, "Low")])
def test_a_category_that_contradicts_its_score_is_rejected(conn: Connection, insert_road: Callable[..., int],
                                                           insert_config: Callable[..., int], value: float, tier: str) -> None:
    expect_db_error(conn, INSERT, {"r": insert_road(), "c": insert_config(), "i": value, "t": tier})


def test_updates_cannot_break_consistency(conn: Connection, insert_road: Callable[..., int],
                                          insert_config: Callable[..., int]) -> None:
    road, cfg = insert_road(), insert_config()
    conn.execute(text(INSERT), {"r": road, "c": cfg, "i": 3.5, "t": "High"})
    expect_db_error(conn, "UPDATE road_vulnerability SET vulnerability_index = 4.2 WHERE road_id = :r", {"r": road})
    expect_db_error(conn, "UPDATE road_vulnerability SET risk_category = 'Severe' WHERE road_id = :r", {"r": road})
    conn.execute(text("UPDATE road_vulnerability SET vulnerability_index = 4.2, risk_category = 'Severe' WHERE road_id = :r"),
                 {"r": road})
    conn.execute(text("UPDATE road_vulnerability SET slope_pct = 3.3 WHERE road_id = :r"), {"r": road})  # unrelated column


def test_category_follows_the_thresholds_of_the_rows_own_configuration(conn: Connection, insert_road: Callable[..., int]) -> None:
    stored = UNEVEN.to_storage()
    cfg = conn.execute(text(
        "INSERT INTO scoring_config (weights, breakpoints, surface_map, tier_thresholds) VALUES "
        "(CAST(:w AS jsonb), CAST(:b AS jsonb), CAST(:s AS jsonb), CAST(:t AS jsonb)) RETURNING config_version"),
        {k[0]: json.dumps(stored[k]) for k in ("weights", "breakpoints", "surface_map", "tier_thresholds")}).scalar_one()
    road = insert_road()
    expect_db_error(conn, INSERT, {"r": road, "c": cfg, "i": 3.5, "t": "High"})   # 3.5 is Severe when Severe starts at 3.333
    conn.execute(text(INSERT), {"r": road, "c": cfg, "i": 3.5, "t": "Severe"})
