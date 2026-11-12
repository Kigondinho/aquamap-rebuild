from __future__ import annotations

import math

import pytest

from core import config_defaults as d


def test_weights_sum_to_one() -> None:
    assert math.isclose(sum(d.DEFAULT_WEIGHTS.values()), 1.0)


def test_weights_cover_the_four_factors() -> None:
    assert set(d.DEFAULT_WEIGHTS) == {"elevation", "river", "slope", "surface"}


def test_tier_thresholds_ascending_and_inside_the_scale() -> None:
    values = [d.DEFAULT_TIER_THRESHOLDS[name] for name in d.TIER_NAMES[1:]]
    assert values == sorted(values)
    assert all(d.SCORE_MIN < v < d.SCORE_MAX for v in values)


@pytest.mark.parametrize("factor", ["elevation", "river", "slope"])
def test_breakpoint_ladders_are_consistent(factor: str) -> None:
    spec = d.DEFAULT_BREAKPOINTS[factor]
    edges, scores = spec["edges"], spec["scores"]
    assert list(edges) == sorted(edges)
    assert len(scores) == len(edges) + 1
    assert all(d.SCORE_MIN <= s <= d.SCORE_MAX for s in scores)
    assert scores[0] == d.SCORE_MAX and scores[-1] == d.SCORE_MIN
    assert list(scores) == sorted(scores, reverse=True)  # lowest bucket is most vulnerable


def test_surface_scores_inside_the_scale() -> None:
    for value in (d.SURFACE_SCORE_UNPAVED, d.SURFACE_SCORE_PAVED, d.SURFACE_SCORE_UNKNOWN):
        assert d.SCORE_MIN <= value <= d.SCORE_MAX
    assert d.SURFACE_SCORE_PAVED < d.SURFACE_SCORE_UNKNOWN < d.SURFACE_SCORE_UNPAVED


def test_surface_tag_sets_do_not_overlap() -> None:
    assert not set(d.SURFACE_PAVED) & set(d.SURFACE_UNPAVED)


def test_nairobi_bbox_is_ordered() -> None:
    min_lon, min_lat, max_lon, max_lat = d.NAIROBI_BBOX
    assert min_lon < max_lon and min_lat < max_lat
