"""Request parsing and classification helpers that need no database."""

from __future__ import annotations

import pytest

from app.errors import InvalidRequestError
from app.services import roads_query as q
from core import ivi
from core.config_defaults import NAIROBI_BBOX, TIER_LABELS, TIER_NAMES


def test_bbox_is_parsed_in_lon_lat_order() -> None:
    box = q.parse_bbox(" 36.78, -1.31 ,36.86,-1.26 ")
    assert box.as_tuple() == (36.78, -1.31, 36.86, -1.26)


def test_bbox_larger_than_the_county_is_clipped_to_the_service_area() -> None:
    box = q.parse_bbox("30,-10,45,10")
    west, south, east, north = NAIROBI_BBOX
    m = q.BBOX_MARGIN_DEG
    assert box.as_tuple() == pytest.approx((west - m, south - m, east + m, north + m))


@pytest.mark.parametrize(("raw", "message"), [
    ("36.78,-1.31,36.86", "four numbers"),
    ("36.78,-1.31,36.86,-1.26,5", "four numbers"),
    ("a,b,c,d", "only numbers"),
    ("36.78,-1.31,nan,-1.26", "finite"),
    ("36.78,-1.31,inf,-1.26", "finite"),
    ("36.86,-1.31,36.78,-1.26", "minLon < maxLon"),
    ("36.78,-1.26,36.86,-1.31", "minLon < maxLon"),
    ("36.78,-1.31,36.78,-1.26", "minLon < maxLon"),
    ("-1.31,36.78,-1.26,36.86", "does not overlap"),   # latitude and longitude swapped
    ("10.0,50.0,11.0,51.0", "does not overlap"),
    ("36.78,-95,36.86,-1.26", "degrees"),
    ("", "four numbers"),
])
def test_bad_bbox_is_rejected(raw: str, message: str) -> None:
    with pytest.raises(InvalidRequestError, match=message):
        q.parse_bbox(raw)


@pytest.mark.parametrize(("lo", "hi"), [(3.5, 3.0), (0.5, None), (None, 5.5), (float("nan"), None)])
def test_bad_ivi_range_is_rejected(lo: float | None, hi: float | None) -> None:
    with pytest.raises(InvalidRequestError):
        q._filters(None, None, lo, hi)


def test_equal_min_and_max_is_a_valid_range() -> None:
    where, params = q._filters(None, None, 3.0, 3.0)
    assert params == {"min_ivi": 3.0, "max_ivi": 3.0} and len(where) == 2


def test_unknown_category_is_rejected_and_values_never_reach_the_sql_text() -> None:
    with pytest.raises(InvalidRequestError, match="Unknown category"):
        q._filters(["Critical"], None, None, None)
    where, params = q._filters(["High", "Severe", "High"], ["primary'; DROP TABLE road_segments;--", " ", "trunk"], 2.0, 4.5)
    sql = " ".join(where)
    assert "DROP" not in sql and "primary" not in sql and "High" not in sql and "4.5" not in sql
    assert params["tiers"] == ["High", "Severe"]
    assert params["highways"] == ["primary'; DROP TABLE road_segments;--", "trunk"]


@pytest.mark.parametrize(("zoom", "tolerance"), [(None, 0.0), (18, 0.0), (15, 0.0), (14, 5.0), (13, 5.0), (12, 20.0),
                                                 (11, 20.0), (10, 60.0), (0, 60.0)])
def test_geometry_is_exact_unless_a_low_zoom_is_given(zoom: int | None, tolerance: float) -> None:
    assert q.simplify_tolerance_m(zoom) == tolerance


def test_like_wildcards_in_search_text_are_escaped() -> None:
    assert q._like_escape("100%_a\\b") == "100\\%\\_a\\\\b"


def test_tier_bounds_describe_four_adjacent_categories() -> None:
    bounds = ivi.tier_bounds(ivi.ScoringConfig())
    assert bounds == [("Low", 1.0, 2.0), ("Medium", 2.0, 3.0), ("High", 3.0, 4.0), ("Severe", 4.0, None)]
    assert [b[0] for b in bounds] == list(TIER_NAMES) and set(TIER_LABELS) == set(TIER_NAMES)
    for (_, _, upper), (_, lower, _) in zip(bounds, bounds[1:], strict=False):
        assert upper == lower  # no gap and no overlap between neighbours
    for name, lower, upper in bounds:
        assert ivi.assign_tier(lower, ivi.ScoringConfig()) == name          # lower bound is inside
        if upper is not None:
            assert ivi.assign_tier(upper, ivi.ScoringConfig()) != name      # upper bound is outside
            assert ivi.assign_tier(round(upper - 0.001, 3), ivi.ScoringConfig()) == name
    assert ivi.assign_tier(5.0, ivi.ScoringConfig()) == "Severe"
