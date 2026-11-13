"""Storing IVI results in PostGIS: upsert, metadata, removal of stale results, SQL re-derivation.

SYNTHETIC roads (the ``insert_road`` fixture); every test runs in a transaction that is rolled back.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import Connection, text

gpd = pytest.importorskip("geopandas")

from core.config_defaults import MODEL_VERSION  # noqa: E402
from core.ivi import ScoringConfig  # noqa: E402
from pipeline import score_roads as sr  # noqa: E402

pytestmark = pytest.mark.db

# (elevation_m, slope_pct, river_dist_m, surface) -> expected index and tier with the default configuration
CASES = [
    ((1480.0, 1.2, 30.0, "dirt"), 5.0, "Severe"),
    ((1652.0, 5.2, 120.0, "asphalt"), 3.15, "High"),
    ((1900.0, 20.0, 2000.0, "asphalt"), 1.15, "Low"),
    ((1652.0, 5.2, 120.0, None), 3.3, "High"),
    ((None, None, 120.0, "asphalt"), None, None),
]


def scored_frame(conn: Connection, road_ids: list[int], cases=CASES):
    frame = gpd.read_postgis(text("SELECT road_id, surface, length_m, geom FROM road_segments WHERE road_id = ANY(:ids) "
                                  "ORDER BY road_id"), conn, geom_col="geom", params={"ids": road_ids})
    inputs = [c[0] for c in cases]
    frame["elevation_m"] = [i[0] if i[0] is not None else float("nan") for i in inputs]
    frame["slope_pct"] = [i[1] if i[1] is not None else float("nan") for i in inputs]
    frame["river_dist_m"] = [i[2] for i in inputs]
    frame["surface"] = [i[3] for i in inputs]
    frame["terrain_samples"] = 12
    return sr.score_segments(frame, ScoringConfig())


@pytest.fixture
def setup(conn: Connection, insert_road: Callable[..., int]):
    conn.execute(text("DELETE FROM road_vulnerability"))  # isolate from real results (rolled back afterwards)
    conn.execute(text("UPDATE scoring_config SET is_active = FALSE"))
    config_version = sr.store_config(conn, ScoringConfig())
    run_id = conn.execute(text("INSERT INTO pipeline_runs (config_version, model_version) VALUES (:c, :m) RETURNING run_id"),
                          {"c": config_version, "m": MODEL_VERSION}).scalar_one()
    roads = [insert_road(name=f"R{i}", dx=i * 200.0) for i in range(len(CASES))]
    return config_version, run_id, roads


def test_results_are_stored_with_expected_values_and_metadata(conn: Connection, setup) -> None:
    config_version, run_id, roads = setup
    counts = sr.persist_scores(conn, scored_frame(conn, roads), config_version, run_id=run_id)
    assert counts == {"results_written": 5, "results_removed_not_eligible": 0}
    rows = conn.execute(text(
        "SELECT road_id, vulnerability_index, risk_category, data_flags, score_elevation, score_slope, score_river, "
        "score_surface, elevation_m, model_version, config_version, run_id, computed_at, terrain_samples "
        "FROM road_vulnerability WHERE road_id = ANY(:ids) ORDER BY road_id"), {"ids": roads}).mappings().all()
    for row, (_inputs, expected, tier) in zip(rows, CASES, strict=True):
        assert (float(row["vulnerability_index"]) if row["vulnerability_index"] is not None else None) == expected
        assert row["risk_category"] == tier
        assert (row["model_version"], row["config_version"], row["run_id"]) == (MODEL_VERSION, config_version, run_id)
        assert row["computed_at"] is not None and row["terrain_samples"] == 12
    assert rows[0]["data_flags"] == [] and rows[3]["data_flags"] == ["surface_missing"]
    assert rows[4]["data_flags"] == ["no_dem", "no_slope"]
    assert rows[4]["elevation_m"] is None and rows[4]["score_elevation"] is None  # missing stays NULL, not 0 or NaN
    assert (rows[4]["score_river"], rows[4]["score_surface"]) == (4, 2)
    assert (rows[1]["score_elevation"], rows[1]["score_slope"], rows[1]["score_river"], rows[1]["score_surface"]) == (3, 3, 4, 2)


def test_stored_results_pass_sql_verification(conn: Connection, setup) -> None:
    config_version, run_id, roads = setup
    sr.persist_scores(conn, scored_frame(conn, roads), config_version, run_id=run_id)
    check = sr.verify_results(conn, config_version, expected_segments=5)
    assert check["ok"], check["errors"]
    assert check["stats"]["scored"] == 4 and check["stats"]["formula_mismatch"] == 0


def test_verification_detects_tampered_results(conn: Connection, setup) -> None:
    config_version, run_id, roads = setup
    sr.persist_scores(conn, scored_frame(conn, roads), config_version, run_id=run_id)
    from tests.db.conftest import expect_db_error

    # a score with the wrong category cannot even be written (trigger from 005_classification.sql) ...
    expect_db_error(conn, "UPDATE road_vulnerability SET vulnerability_index = 4.5, risk_category = 'High' "
                          "WHERE road_id = :r", {"r": roads[1]})
    # ... but a self-consistent score that no longer follows from its factor scores can, and is caught here
    conn.execute(text("UPDATE road_vulnerability SET vulnerability_index = 4.5, risk_category = 'Severe' WHERE road_id = :r"),
                 {"r": roads[1]})
    conn.execute(text("UPDATE road_vulnerability SET data_flags = '{}' WHERE road_id = :r"), {"r": roads[4]})
    check = sr.verify_results(conn, config_version, expected_segments=6)
    assert not check["ok"]
    stats = check["stats"]
    assert (stats["formula_mismatch"], stats["tier_mismatch"], stats["unexplained_null"]) == (1, 0, 1)
    assert any("expected 6" in e for e in check["errors"])


def test_rescoring_replaces_results_without_duplicates(conn: Connection, setup) -> None:
    config_version, run_id, roads = setup
    sr.persist_scores(conn, scored_frame(conn, roads), config_version, run_id=run_id)
    changed = [((1480.0, 1.2, 30.0, "asphalt"), 4.55, "Severe"), *CASES[1:]]
    counts = sr.persist_scores(conn, scored_frame(conn, roads, changed), config_version, run_id=run_id)
    assert counts["results_written"] == 5
    assert conn.execute(text("SELECT count(*) FROM road_vulnerability")).scalar_one() == 5
    assert float(conn.execute(text("SELECT vulnerability_index FROM road_vulnerability WHERE road_id = :r"),
                              {"r": roads[0]}).scalar_one()) == 4.55


def test_results_of_roads_that_are_no_longer_eligible_are_removed(conn: Connection, setup) -> None:
    config_version, run_id, roads = setup
    sr.persist_scores(conn, scored_frame(conn, roads), config_version, run_id=run_id)
    counts = sr.persist_scores(conn, scored_frame(conn, roads[:3], CASES[:3]), config_version, run_id=run_id)
    assert counts == {"results_written": 3, "results_removed_not_eligible": 2}
    assert conn.execute(text("SELECT count(*) FROM road_vulnerability")).scalar_one() == 3


def test_view_exposes_scores_and_metadata(conn: Connection, setup) -> None:
    config_version, run_id, roads = setup
    sr.persist_scores(conn, scored_frame(conn, roads), config_version, run_id=run_id)
    row = conn.execute(text("SELECT vulnerability_index, risk_category, model_version, terrain_samples, computed_at "
                            "FROM v_road_risk WHERE road_id = :r"), {"r": roads[0]}).one()
    assert (float(row[0]), row[1], row[2], row[3]) == (5.0, "Severe", MODEL_VERSION, 12) and row[4] is not None


def test_active_configuration_round_trip_and_single_active(conn: Connection, setup) -> None:
    config_version, _, _ = setup
    loaded = sr.load_active_config(conn)
    assert loaded is not None and loaded[0] == config_version
    assert loaded[1].weights == ScoringConfig().weights and loaded[1].breakpoints["slope"]["edges"] == (2.0, 5.0, 10.0, 15.0)
    newer = sr.store_config(conn, ScoringConfig())
    assert newer != config_version
    assert conn.execute(text("SELECT array_agg(config_version) FROM scoring_config WHERE is_active")).scalar_one() == [newer]


def test_configuration_on_the_old_scale_is_refused(conn: Connection, setup) -> None:
    conn.execute(text("UPDATE scoring_config SET tier_thresholds = '{\"Medium\": 0.25, \"High\": 0.5, \"Severe\": 0.75}' "
                      "WHERE is_active"))
    with pytest.raises(sr.ScoringError, match="--new-config"):
        sr.load_active_config(conn)


def test_database_rejects_values_off_the_scale(conn: Connection, setup) -> None:
    from tests.db.conftest import expect_db_error

    config_version, _, roads = setup
    for ivi_value, tier in ((0.5, "Low"), (5.2, "Severe")):
        expect_db_error(conn, "INSERT INTO road_vulnerability (road_id, config_version, vulnerability_index, risk_category) "
                              "VALUES (:r, :c, :i, :t)", {"r": roads[0], "c": config_version, "i": ivi_value, "t": tier})
