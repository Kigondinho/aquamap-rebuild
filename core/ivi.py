"""Infrastructure Vulnerability Index (IVI): Weighted Linear Combination on a 1-5 scale.

    IVI = (WE x E) + (WS x S) + (WD x D) + (WM x M)

E elevation, S slope, D distance to the nearest river, M road surface. Each factor is first
normalised to a vulnerability score from 1 to 5 (5 = most vulnerable); the weights sum to 1, so the
IVI is also between 1 and 5. A tier (Low, Medium, High, Severe) is read from the IVI.

What the IVI is: a relative, static ranking of road segments by terrain and surface characteristics
that make surface flooding more likely to affect them. What it is NOT: a flood probability, a flood
depth, a forecast or a measured flood risk. It uses no rainfall, drainage or flood-record data and
has not been validated against observed floods (see ``INTERPRETATION_NOTE``).

Everything here is pure: no database, no files, no globals. Missing inputs are never replaced by an
invented measurement; each case has an explicit, documented outcome and a flag:

* elevation or slope missing  -> no IVI (``None``), flag ``no_dem`` / ``no_slope``
* river distance missing      -> D = 1 (nothing nearby is the least vulnerable class), flag ``no_river``
* surface missing             -> M = neutral midpoint, flag ``surface_missing``
* surface tag not recognised  -> M = neutral midpoint, flag ``surface_unrecognised``
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core import config_defaults as defaults

FACTORS: tuple[str, ...] = ("elevation", "slope", "river", "surface")
NUMERIC_FACTORS: tuple[str, ...] = ("elevation", "slope", "river")
NON_NEGATIVE: frozenset[str] = frozenset({"slope", "river"})
IVI_DECIMALS = 3  # stored precision; the tier is read from the rounded value so the two always agree

FLAG_NO_DEM = "no_dem"
FLAG_NO_SLOPE = "no_slope"
FLAG_NO_RIVER = "no_river"
FLAG_SURFACE_MISSING = "surface_missing"
FLAG_SURFACE_UNRECOGNISED = "surface_unrecognised"

INTERPRETATION_NOTE = (
    "The Infrastructure Vulnerability Index is a relative screening index (1 = least, 5 = most vulnerable) "
    "built from elevation, slope, distance to mapped waterways and road surface. It is not a flood "
    "probability, a flood forecast or a measured flood risk: it uses no rainfall, drainage or flood-record "
    "data and has not been validated against observed flooding."
)


def _default_surface_scores() -> dict[str, float]:
    scores = {tag: defaults.SURFACE_SCORE_UNPAVED for tag in defaults.SURFACE_UNPAVED}
    scores.update({tag: defaults.SURFACE_SCORE_PAVED for tag in defaults.SURFACE_PAVED})
    return scores


@dataclass(frozen=True, slots=True)
class FactorScores:
    """Normalised factor scores, each 1..5 (higher = more vulnerable)."""

    elevation: float
    slope: float
    river: float
    surface: float


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Everything the IVI needs; mirrors one row of the ``scoring_config`` table."""

    weights: Mapping[str, float] = field(default_factory=lambda: dict(defaults.DEFAULT_WEIGHTS))
    breakpoints: Mapping[str, Mapping[str, tuple[float, ...]]] = field(
        default_factory=lambda: {k: dict(v) for k, v in defaults.DEFAULT_BREAKPOINTS.items()}
    )
    surface_scores: Mapping[str, float] = field(default_factory=_default_surface_scores)
    surface_unknown_score: float = defaults.SURFACE_SCORE_UNKNOWN
    tier_thresholds: Mapping[str, float] = field(default_factory=lambda: dict(defaults.DEFAULT_TIER_THRESHOLDS))

    # --- storage form (JSON columns of ``scoring_config``)
    def to_storage(self) -> dict[str, Any]:
        return {
            "weights": dict(self.weights),
            "breakpoints": {f: {k: list(v) for k, v in spec.items()} for f, spec in self.breakpoints.items()},
            "surface_map": {**self.surface_scores, defaults.SURFACE_UNKNOWN_KEY: self.surface_unknown_score},
            "tier_thresholds": dict(self.tier_thresholds),
        }

    @classmethod
    def from_storage(cls, weights: Mapping[str, float], breakpoints: Mapping[str, Mapping[str, Any]],
                     surface_map: Mapping[str, float], tier_thresholds: Mapping[str, float]) -> ScoringConfig:
        surface = {k: float(v) for k, v in surface_map.items() if k != defaults.SURFACE_UNKNOWN_KEY}
        if defaults.SURFACE_UNKNOWN_KEY not in surface_map:
            raise ValueError(f"surface map has no '{defaults.SURFACE_UNKNOWN_KEY}' entry for unknown surfaces")
        config = cls(
            weights={k: float(v) for k, v in weights.items()},
            breakpoints={f: {k: tuple(float(x) for x in v) for k, v in spec.items()} for f, spec in breakpoints.items()},
            surface_scores=surface,
            surface_unknown_score=float(surface_map[defaults.SURFACE_UNKNOWN_KEY]),
            tier_thresholds={k: float(v) for k, v in tier_thresholds.items()},
        )
        validate_config(config)
        return config


@dataclass(frozen=True, slots=True)
class SegmentScore:
    """Result for one road segment. A factor score or the IVI is ``None`` only when it could not be computed."""

    score_elevation: float | None
    score_slope: float | None
    score_river: float | None
    score_surface: float | None
    ivi: float | None
    tier: str | None
    flags: tuple[str, ...]

    @property
    def scored(self) -> bool:
        return self.ivi is not None


# ---------------------------------------------------------------------------- helpers
def is_missing(value: object) -> bool:
    """True for ``None`` and NaN (how missing measurements arrive from NumPy/pandas)."""
    return value is None or (isinstance(value, float) and math.isnan(value))


def _in_scale(value: float) -> bool:
    return math.isfinite(value) and defaults.SCORE_MIN <= value <= defaults.SCORE_MAX


def validate_config(config: ScoringConfig) -> None:
    """Raise ``ValueError`` describing the first problem found in ``config``."""
    if set(config.weights) != set(FACTORS):
        raise ValueError(f"weights must be given for exactly {sorted(FACTORS)}, got {sorted(config.weights)}")
    for name, w in config.weights.items():
        if not math.isfinite(w) or w < 0:
            raise ValueError(f"weight '{name}' must be a finite number >= 0, got {w}")
    total = math.fsum(config.weights.values())
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError(f"weights must sum to 1.0, got {total:.6f}")

    if set(config.breakpoints) != set(NUMERIC_FACTORS):
        raise ValueError(f"breakpoints must be given for exactly {sorted(NUMERIC_FACTORS)}")
    for name, spec in config.breakpoints.items():
        edges, scores = tuple(spec.get("edges", ())), tuple(spec.get("scores", ()))
        if not edges:
            raise ValueError(f"{name}: at least one breakpoint is needed")
        if len(scores) != len(edges) + 1:
            raise ValueError(f"{name}: need {len(edges) + 1} scores for {len(edges)} breakpoints, got {len(scores)}")
        if not all(math.isfinite(e) for e in edges) or any(b <= a for a, b in zip(edges, edges[1:], strict=False)):
            raise ValueError(f"{name}: breakpoints must be finite and strictly ascending, got {edges}")
        if not all(_in_scale(s) for s in scores):
            raise ValueError(f"{name}: scores must be between {defaults.SCORE_MIN} and {defaults.SCORE_MAX}, got {scores}")
        if any(b > a for a, b in zip(scores, scores[1:], strict=False)):
            raise ValueError(f"{name}: scores must not increase with the measured value (lower value = more vulnerable)")

    for tag, s in {**config.surface_scores, "(unknown)": config.surface_unknown_score}.items():
        if not _in_scale(s):
            raise ValueError(f"surface score for '{tag}' must be between {defaults.SCORE_MIN} and {defaults.SCORE_MAX}")

    expected = defaults.TIER_NAMES[1:]
    if set(config.tier_thresholds) != set(expected):
        raise ValueError(f"tier thresholds must be given for exactly {list(expected)}")
    cuts = [config.tier_thresholds[name] for name in expected]
    if any(b <= a for a, b in zip(cuts, cuts[1:], strict=False)):
        raise ValueError(f"tier thresholds must be strictly ascending in the order {list(expected)}, got {cuts}")
    if not all(math.isfinite(c) and defaults.SCORE_MIN < c <= defaults.SCORE_MAX for c in cuts):
        raise ValueError(f"tier thresholds must lie above {defaults.SCORE_MIN} and at most {defaults.SCORE_MAX}, got {cuts}")


# ---------------------------------------------------------------------------- factor normalisation
def normalise_numeric(value: float, factor: str, config: ScoringConfig) -> float:
    """Map elevation (m), slope (%) or river distance (m) to a 1..5 score.

    The value falls in the first class whose upper bound is greater than it; a value exactly on a
    breakpoint belongs to the class above (``< 1500 m`` is class one, ``1500 m`` is class two).
    Missing, non-finite and (for slope and distance) negative values raise ``ValueError``: the caller
    decides what a missing input means, this function never guesses.
    """
    if factor not in NUMERIC_FACTORS:
        raise ValueError(f"unknown numeric factor '{factor}'")
    if is_missing(value) or not math.isfinite(value):
        raise ValueError(f"{factor}: a finite value is required, got {value!r}")
    if factor in NON_NEGATIVE and value < 0:
        raise ValueError(f"{factor} cannot be negative, got {value}")
    spec = config.breakpoints[factor]
    for edge, score in zip(spec["edges"], spec["scores"], strict=False):
        if value < edge:
            return float(score)
    return float(spec["scores"][-1])


def normalise_surface(surface: str | None, config: ScoringConfig | None = None) -> tuple[float, str | None]:
    """Map an OSM ``surface`` tag to ``(score 1..5, flag)``.

    A known tag gives its score and no flag. A missing or blank tag gives the neutral score with
    ``surface_missing``; an unrecognised tag gives the neutral score with ``surface_unrecognised``.
    Matching ignores case and surrounding spaces; for a list such as ``asphalt;gravel`` the first
    value is used; a qualified value such as ``concrete:plates`` is matched by its part before the colon.
    """
    config = config or ScoringConfig()
    if is_missing(surface) or not str(surface).strip():
        return float(config.surface_unknown_score), FLAG_SURFACE_MISSING
    tag = str(surface).strip().lower().split(";")[0].strip()
    for candidate in (tag, tag.split(":")[0]):
        if candidate in config.surface_scores:
            return float(config.surface_scores[candidate]), None
    return float(config.surface_unknown_score), FLAG_SURFACE_UNRECOGNISED


# ---------------------------------------------------------------------------- combination
def compute_ivi(scores: FactorScores, config: ScoringConfig) -> float:
    """Weighted linear combination of the four factor scores, rounded to three decimals (1..5)."""
    values = {"elevation": scores.elevation, "slope": scores.slope, "river": scores.river, "surface": scores.surface}
    for name, v in values.items():
        if is_missing(v) or not _in_scale(v):
            raise ValueError(f"{name} score must be between {defaults.SCORE_MIN} and {defaults.SCORE_MAX}, got {v!r}")
    ivi = math.fsum(config.weights[name] * v for name, v in values.items())
    return round(min(max(ivi, defaults.SCORE_MIN), defaults.SCORE_MAX), IVI_DECIMALS)


def assign_tier(ivi: float, config: ScoringConfig) -> str:
    """Tier for an IVI value. A value exactly on a threshold belongs to the higher tier."""
    if is_missing(ivi) or not _in_scale(ivi):
        raise ValueError(f"IVI must be between {defaults.SCORE_MIN} and {defaults.SCORE_MAX}, got {ivi!r}")
    tier = defaults.TIER_NAMES[0]
    for name in defaults.TIER_NAMES[1:]:
        if ivi >= config.tier_thresholds[name]:
            tier = name
    return tier


def tier_bounds(config: ScoringConfig) -> list[tuple[str, float, float | None]]:
    """``(tier, lower bound, upper bound)`` from lowest to highest tier.

    The lower bound is inclusive and the upper bound exclusive, except for the top tier, whose upper
    bound is ``None`` (it runs to the top of the scale, inclusive).
    """
    lowers = [defaults.SCORE_MIN, *(config.tier_thresholds[name] for name in defaults.TIER_NAMES[1:])]
    uppers: list[float | None] = [*lowers[1:], None]
    return list(zip(defaults.TIER_NAMES, lowers, uppers, strict=True))


def score_segment(elevation_m: float | None, slope_pct: float | None, river_dist_m: float | None,
                  surface: str | None, config: ScoringConfig) -> SegmentScore:
    """Score one road segment from its measured inputs, applying the missing-data policy.

    Factor scores that can be computed are always returned, so a segment without an IVI still shows
    what is known about it.
    """
    flags: list[str] = []
    e = s = None
    if is_missing(elevation_m):
        flags.append(FLAG_NO_DEM)
    else:
        e = normalise_numeric(float(elevation_m), "elevation", config)
    if is_missing(slope_pct):
        flags.append(FLAG_NO_SLOPE)
    else:
        s = normalise_numeric(float(slope_pct), "slope", config)
    if is_missing(river_dist_m):
        d = float(config.breakpoints["river"]["scores"][-1])  # no mapped waterway: the farthest class
        flags.append(FLAG_NO_RIVER)
    else:
        d = normalise_numeric(float(river_dist_m), "river", config)
    m, surface_flag = normalise_surface(surface, config)
    if surface_flag:
        flags.append(surface_flag)

    if e is None or s is None:  # terrain is 55 % of the weight: no index without it
        return SegmentScore(e, s, d, m, None, None, tuple(flags))
    ivi = compute_ivi(FactorScores(e, s, d, m), config)
    return SegmentScore(e, s, d, m, ivi, assign_tier(ivi, config), tuple(flags))
