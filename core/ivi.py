"""Infrastructure Vulnerability Index (IVI): Weighted Linear Combination.

Phase 2 scaffold: data structures and function signatures only. The calculations
are implemented in a later phase together with their unit tests.

All functions here are pure: no database, no files, no globals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from core import config_defaults as defaults


@dataclass(frozen=True, slots=True)
class FactorScores:
    """Normalised factor scores, each in the range 0..1 (higher = more vulnerable)."""

    elevation: float
    river: float
    slope: float
    surface: float


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Everything the IVI needs; mirrors one row of the ``scoring_config`` table."""

    weights: Mapping[str, float] = field(default_factory=lambda: dict(defaults.DEFAULT_WEIGHTS))
    breakpoints: Mapping[str, Mapping[str, tuple[float, ...]]] = field(
        default_factory=lambda: dict(defaults.DEFAULT_BREAKPOINTS)
    )
    tier_thresholds: Mapping[str, float] = field(
        default_factory=lambda: dict(defaults.DEFAULT_TIER_THRESHOLDS)
    )


def validate_config(config: ScoringConfig) -> None:
    """Raise ``ValueError`` if weights do not sum to 1, thresholds are not ascending, etc."""
    raise NotImplementedError("IVI configuration validation")


def normalise_numeric(value: float, factor: str, config: ScoringConfig) -> float:
    """Map a raw elevation (m), river distance (m) or slope (%) to a 0..1 score."""
    raise NotImplementedError("numeric factor normalisation")


def normalise_surface(surface: str | None) -> float:
    """Map an OSM ``surface`` tag to a 0..1 susceptibility score."""
    raise NotImplementedError("surface normalisation")


def compute_ivi(scores: FactorScores, config: ScoringConfig) -> float:
    """Combine factor scores into a single 0..1 index using the configured weights."""
    raise NotImplementedError("weighted linear combination")


def assign_tier(ivi: float, config: ScoringConfig) -> str:
    """Return ``Low``, ``Medium``, ``High`` or ``Severe`` for an IVI value."""
    raise NotImplementedError("tier assignment")
