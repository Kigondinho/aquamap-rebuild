"""IVI engine: known values, class boundaries, missing data and configuration checks. No I/O."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from core import config_defaults as d
from core import ivi
from core.ivi import FactorScores, ScoringConfig

CFG = ScoringConfig()


# ---------------------------------------------------------------- configuration
def test_default_weights_are_the_documented_baseline_and_sum_to_one() -> None:
    assert CFG.weights == {"elevation": 0.35, "slope": 0.20, "river": 0.30, "surface": 0.15}
    assert math.isclose(math.fsum(CFG.weights.values()), 1.0, abs_tol=1e-12)
    ivi.validate_config(CFG)


@pytest.mark.parametrize(("change", "message"), [
    ({"weights": {"elevation": 0.35, "slope": 0.20, "river": 0.30, "surface": 0.20}}, "sum to 1.0"),
    ({"weights": {"elevation": 0.5, "slope": 0.5, "river": 0.0}}, "exactly"),
    ({"weights": {"elevation": 1.2, "slope": -0.2, "river": 0.0, "surface": 0.0}}, ">= 0"),
    ({"weights": {"elevation": float("nan"), "slope": 0.2, "river": 0.3, "surface": 0.15}}, "finite"),
    ({"breakpoints": {**CFG.breakpoints, "slope": {"edges": (5.0, 2.0), "scores": (5.0, 3.0, 1.0)}}}, "ascending"),
    ({"breakpoints": {**CFG.breakpoints, "slope": {"edges": (2.0, 5.0), "scores": (5.0, 1.0)}}}, "scores for"),
    ({"breakpoints": {**CFG.breakpoints, "slope": {"edges": (2.0,), "scores": (6.0, 1.0)}}}, "between"),
    ({"breakpoints": {**CFG.breakpoints, "slope": {"edges": (2.0,), "scores": (1.0, 5.0)}}}, "must not increase"),
    ({"breakpoints": {"elevation": CFG.breakpoints["elevation"]}}, "exactly"),
    ({"surface_unknown_score": 0.5}, "surface score"),
    ({"tier_thresholds": {"Medium": 3.0, "High": 2.0, "Severe": 4.0}}, "ascending"),
    ({"tier_thresholds": {"Medium": 2.0, "High": 3.0}}, "exactly"),
    ({"tier_thresholds": {"Medium": 1.0, "High": 3.0, "Severe": 4.0}}, "must lie above"),
])
def test_invalid_configurations_are_rejected(change: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ivi.validate_config(replace(CFG, **change))


def test_configuration_survives_storage_round_trip() -> None:
    stored = CFG.to_storage()
    assert stored["surface_map"]["*"] == d.SURFACE_SCORE_UNKNOWN
    back = ScoringConfig.from_storage(**stored)
    assert back.weights == CFG.weights and back.tier_thresholds == CFG.tier_thresholds
    assert back.surface_scores == CFG.surface_scores and back.surface_unknown_score == CFG.surface_unknown_score
    assert back.breakpoints["river"]["edges"] == (50.0, 150.0, 300.0, 500.0)


def test_stored_configuration_on_the_old_scale_is_rejected() -> None:
    stored = CFG.to_storage()
    stored["tier_thresholds"] = {"Medium": 0.25, "High": 0.5, "Severe": 0.75}
    with pytest.raises(ValueError):
        ScoringConfig.from_storage(**stored)
    del stored["surface_map"]["*"]
    with pytest.raises(ValueError, match="unknown surfaces"):
        ScoringConfig.from_storage(**stored)


# ---------------------------------------------------------------- factor normalisation: boundaries
@pytest.mark.parametrize(("factor", "value", "expected"), [
    # elevation (m): lower ground = more vulnerable
    ("elevation", 1400.0, 5), ("elevation", 1499.999, 5), ("elevation", 1500.0, 4), ("elevation", 1599.999, 4),
    ("elevation", 1600.0, 3), ("elevation", 1700.0, 2), ("elevation", 1799.999, 2), ("elevation", 1800.0, 1),
    ("elevation", 2300.0, 1), ("elevation", -5.0, 5),
    # slope (%): flatter = more vulnerable (water ponds)
    ("slope", 0.0, 5), ("slope", 1.999, 5), ("slope", 2.0, 4), ("slope", 4.999, 4), ("slope", 5.0, 3),
    ("slope", 10.0, 2), ("slope", 14.999, 2), ("slope", 15.0, 1), ("slope", 82.5, 1),
    # river distance (m): closer = more vulnerable
    ("river", 0.0, 5), ("river", 49.999, 5), ("river", 50.0, 4), ("river", 150.0, 3), ("river", 299.999, 3),
    ("river", 300.0, 2), ("river", 500.0, 1), ("river", 3934.0, 1),
])
def test_numeric_factor_classes_and_boundaries(factor: str, value: float, expected: int) -> None:
    assert ivi.normalise_numeric(value, factor, CFG) == expected


@pytest.mark.parametrize("factor", ivi.NUMERIC_FACTORS)
def test_scores_never_increase_as_the_measurement_grows(factor: str) -> None:
    values = [x * 7.3 for x in range(0, 400)]
    scores = [ivi.normalise_numeric(v, factor, CFG) for v in values]
    assert all(b <= a for a, b in zip(scores, scores[1:], strict=False))
    assert set(scores) <= {1.0, 2.0, 3.0, 4.0, 5.0}


@pytest.mark.parametrize(("factor", "value"), [
    ("elevation", None), ("elevation", float("nan")), ("elevation", float("inf")), ("slope", -0.1),
    ("river", -1.0), ("river", float("-inf")), ("rainfall", 10.0),
])
def test_numeric_normalisation_refuses_missing_and_impossible_values(factor: str, value: float | None) -> None:
    with pytest.raises(ValueError):
        ivi.normalise_numeric(value, factor, CFG)  # type: ignore[arg-type]


@pytest.mark.parametrize(("tag", "score", "flag"), [
    ("asphalt", 2, None), ("paved", 2, None), ("concrete", 2, None), ("paving_stones", 2, None), ("sett", 2, None),
    ("unpaved", 5, None), ("dirt", 5, None), ("ground", 5, None), ("gravel", 5, None), ("compacted", 5, None),
    ("  Asphalt ", 2, None), ("DIRT", 5, None),
    ("dirt;ground", 5, None), ("paving_stones:lanes", 2, None), ("concrete:plates", 2, None),
    (None, 3, "surface_missing"), ("", 3, "surface_missing"), ("   ", 3, "surface_missing"),
    (float("nan"), 3, "surface_missing"),
    ("tarmac", 3, "surface_unrecognised"), ("wood", 3, "surface_unrecognised"), ("yes", 3, "surface_unrecognised"),
])
def test_surface_normalisation(tag: str | None, score: int, flag: str | None) -> None:
    assert ivi.normalise_surface(tag, CFG) == (score, flag)


def test_surface_ordering() -> None:
    assert d.SURFACE_SCORE_PAVED < d.SURFACE_SCORE_UNKNOWN < d.SURFACE_SCORE_UNPAVED


# ---------------------------------------------------------------- weighted linear combination
@pytest.mark.parametrize(("e", "s", "dist", "m", "expected"), [
    (5, 5, 5, 5, 5.0),
    (1, 1, 1, 1, 1.0),
    (3, 3, 3, 3, 3.0),
    (5, 1, 1, 1, 2.4),    # 0.35*5 + 0.20 + 0.30 + 0.15
    (1, 5, 1, 1, 1.8),    # 0.35 + 0.20*5 + 0.30 + 0.15
    (1, 1, 5, 1, 2.2),    # 0.35 + 0.20 + 0.30*5 + 0.15
    (1, 1, 1, 5, 1.6),    # 0.35 + 0.20 + 0.30 + 0.15*5
    (4, 3, 5, 2, 3.8),    # 1.40 + 0.60 + 1.50 + 0.30
    (3, 4, 2, 3, 2.9),    # 1.05 + 0.80 + 0.60 + 0.45
    (2, 5, 4, 3, 3.35),   # 0.70 + 1.00 + 1.20 + 0.45
])
def test_ivi_known_values(e: float, s: float, dist: float, m: float, expected: float) -> None:
    assert ivi.compute_ivi(FactorScores(e, s, dist, m), CFG) == expected


def test_each_weight_is_the_effect_of_one_factor() -> None:
    """Raising one factor by one class raises the IVI by exactly that factor's weight."""
    base = ivi.compute_ivi(FactorScores(2, 2, 2, 2), CFG)
    assert ivi.compute_ivi(FactorScores(3, 2, 2, 2), CFG) == pytest.approx(base + 0.35)
    assert ivi.compute_ivi(FactorScores(2, 3, 2, 2), CFG) == pytest.approx(base + 0.20)
    assert ivi.compute_ivi(FactorScores(2, 2, 3, 2), CFG) == pytest.approx(base + 0.30)
    assert ivi.compute_ivi(FactorScores(2, 2, 2, 3), CFG) == pytest.approx(base + 0.15)


def test_ivi_stays_on_the_scale_for_every_class_combination() -> None:
    values = [ivi.compute_ivi(FactorScores(e, s, dist, m), CFG)
              for e in range(1, 6) for s in range(1, 6) for dist in range(1, 6) for m in (2, 3, 5)]
    assert min(values) >= 1.0 and max(values) == 5.0
    assert all(round(v, 3) == v for v in values)


def test_ivi_uses_the_configured_weights() -> None:
    cfg = replace(CFG, weights={"elevation": 0.25, "slope": 0.25, "river": 0.25, "surface": 0.25})
    assert ivi.compute_ivi(FactorScores(5, 1, 3, 3), cfg) == 3.0
    only_slope = replace(CFG, weights={"elevation": 0.0, "slope": 1.0, "river": 0.0, "surface": 0.0})
    assert ivi.compute_ivi(FactorScores(5, 2, 5, 5), only_slope) == 2.0


@pytest.mark.parametrize("bad", [0.99, 5.01, float("nan"), None, float("inf")])
def test_ivi_refuses_scores_off_the_scale(bad: float | None) -> None:
    with pytest.raises(ValueError):
        ivi.compute_ivi(FactorScores(bad, 3, 3, 3), CFG)  # type: ignore[arg-type]


# ---------------------------------------------------------------- tiers
@pytest.mark.parametrize(("value", "tier"), [
    (1.0, "Low"), (1.999, "Low"), (2.0, "Medium"), (2.999, "Medium"), (3.0, "High"), (3.999, "High"),
    (4.0, "Severe"), (5.0, "Severe"),
])
def test_tier_boundaries(value: float, tier: str) -> None:
    assert ivi.assign_tier(value, CFG) == tier


@pytest.mark.parametrize("bad", [0.5, 5.5, float("nan"), None])
def test_tier_refuses_values_off_the_scale(bad: float | None) -> None:
    with pytest.raises(ValueError):
        ivi.assign_tier(bad, CFG)  # type: ignore[arg-type]


def test_floating_point_sums_land_on_the_right_side_of_a_threshold() -> None:
    """All fours is exactly 4.0 = Severe, even though 0.35*4 + 0.2*4 + 0.3*4 + 0.15*4 is not exact in binary."""
    result = ivi.score_segment(1550.0, 3.0, 100.0, None, replace(CFG, surface_unknown_score=4.0))
    assert (result.score_elevation, result.score_slope, result.score_river, result.score_surface) == (4, 4, 4, 4)
    assert result.ivi == 4.0 and result.tier == "Severe"


# ---------------------------------------------------------------- whole segments, including missing data
def test_segment_with_all_inputs() -> None:
    r = ivi.score_segment(elevation_m=1480.0, slope_pct=1.2, river_dist_m=30.0, surface="dirt", config=CFG)
    assert (r.score_elevation, r.score_slope, r.score_river, r.score_surface) == (5, 5, 5, 5)
    assert (r.ivi, r.tier, r.flags, r.scored) == (5.0, "Severe", (), True)


def test_segment_least_vulnerable_case() -> None:
    r = ivi.score_segment(1900.0, 20.0, 2000.0, "asphalt", CFG)
    assert (r.score_elevation, r.score_slope, r.score_river, r.score_surface) == (1, 1, 1, 2)
    assert r.ivi == 1.15 and r.tier == "Low" and r.flags == ()


def test_segment_worked_example() -> None:
    """Elevation 1652 m -> 3, slope 5.2 % -> 3, river 120 m -> 4, asphalt -> 2: 1.05 + 0.60 + 1.20 + 0.30 = 3.15."""
    r = ivi.score_segment(1652.0, 5.2, 120.0, "asphalt", CFG)
    assert r.ivi == 3.15 and r.tier == "High"


def test_missing_surface_uses_the_neutral_class_and_is_flagged() -> None:
    r = ivi.score_segment(1652.0, 5.2, 120.0, None, CFG)
    assert r.score_surface == 3 and r.flags == ("surface_missing",)
    assert r.ivi == 3.3 and r.tier == "High"  # 1.05 + 0.60 + 1.20 + 0.45


def test_unrecognised_surface_is_flagged_differently_from_missing() -> None:
    assert ivi.score_segment(1652.0, 5.2, 120.0, "tarmac", CFG).flags == ("surface_unrecognised",)


@pytest.mark.parametrize(("elevation", "slope", "flags"), [
    (None, 5.2, ("no_dem",)), (float("nan"), 5.2, ("no_dem",)),
    (1652.0, None, ("no_slope",)), (1652.0, float("nan"), ("no_slope",)),
    (None, None, ("no_dem", "no_slope")),
])
def test_missing_terrain_gives_no_index_never_a_guess(elevation: float | None, slope: float | None, flags: tuple) -> None:
    r = ivi.score_segment(elevation, slope, 120.0, "asphalt", CFG)
    assert r.ivi is None and r.tier is None and not r.scored
    assert r.flags == flags
    assert r.score_river == 4 and r.score_surface == 2  # what is known is still reported
    assert (r.score_elevation is None) == ("no_dem" in flags) and (r.score_slope is None) == ("no_slope" in flags)


def test_no_mapped_river_is_the_farthest_class_and_is_flagged() -> None:
    r = ivi.score_segment(1652.0, 5.2, None, "asphalt", CFG)
    assert r.score_river == 1 and "no_river" in r.flags
    assert r.ivi == 2.25  # 1.05 + 0.60 + 0.30 + 0.30


def test_all_flags_can_combine() -> None:
    r = ivi.score_segment(None, None, float("nan"), None, CFG)
    assert r.ivi is None and set(r.flags) == {"no_dem", "no_slope", "no_river", "surface_missing"}


def test_impossible_measurements_raise_instead_of_being_scored() -> None:
    with pytest.raises(ValueError):
        ivi.score_segment(1652.0, -3.0, 120.0, "asphalt", CFG)
    with pytest.raises(ValueError):
        ivi.score_segment(1652.0, 3.0, -120.0, "asphalt", CFG)


def test_interpretation_note_says_what_the_index_is_not() -> None:
    note = ivi.INTERPRETATION_NOTE.lower()
    assert "not a flood" in note and "probability" in note and "not been validated" in note
