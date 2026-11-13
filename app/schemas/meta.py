from __future__ import annotations

from pydantic import BaseModel


class TierDefinition(BaseModel):
    """``lower_bound <= IVI < upper_bound``; the top tier has no upper bound and includes the scale maximum."""

    tier: str
    lower_bound: float
    upper_bound: float | None = None
    plain_label: str


class MetaConfigResponse(BaseModel):
    """Single source of truth for the UI: tier cut-offs, labels and weights."""

    tiers: list[TierDefinition]
    boundary_rule: str
    scale_min: float
    scale_max: float
    weights: dict[str, float]
    config_version: int | None = None
    disclaimer: str
