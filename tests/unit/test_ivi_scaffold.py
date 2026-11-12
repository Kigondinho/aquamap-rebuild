"""Locks the Phase 2 state: IVI types exist but no scoring logic yet.

These tests are replaced by real calculation tests when the scoring engine is built.
"""

from __future__ import annotations

import pytest

from core import ivi


def test_default_scoring_config_uses_project_defaults() -> None:
    config = ivi.ScoringConfig()
    assert config.weights["elevation"] == 0.35
    assert set(config.tier_thresholds) == {"Medium", "High", "Severe"}


@pytest.mark.parametrize(
    "call",
    [
        lambda: ivi.validate_config(ivi.ScoringConfig()),
        lambda: ivi.normalise_numeric(1650.0, "elevation", ivi.ScoringConfig()),
        lambda: ivi.normalise_surface("asphalt"),
        lambda: ivi.compute_ivi(ivi.FactorScores(0.5, 0.5, 0.5, 0.5), ivi.ScoringConfig()),
        lambda: ivi.assign_tier(0.5, ivi.ScoringConfig()),
    ],
)
def test_scoring_functions_not_implemented_yet(call) -> None:
    with pytest.raises(NotImplementedError):
        call()
