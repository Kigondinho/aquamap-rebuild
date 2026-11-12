from __future__ import annotations

from pydantic import BaseModel


class TierDefinition(BaseModel):
    tier: str
    lower_bound: float
    plain_label: str


class MetaConfigResponse(BaseModel):
    """Single source of truth for the UI: tier cut-offs, labels and weights."""

    tiers: list[TierDefinition]
    weights: dict[str, float]
    config_version: int | None = None
    disclaimer: str
