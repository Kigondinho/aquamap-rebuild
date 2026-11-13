from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.common import Tier


class RoadSummaryProperties(BaseModel):
    """Light properties returned for map drawing."""

    id: int
    name: str | None = None
    highway_type: str
    risk_category: Tier | None = None
    vulnerability_index: float | None = None


class FactorBreakdown(BaseModel):
    """One value per factor: class scores (1-5) or weights (sum to 1)."""

    elevation: float | None = None
    slope: float | None = None
    river: float | None = None
    surface: float | None = None


class RoadDetailProperties(RoadSummaryProperties):
    """Full properties for the detail popup (explainability, NFR-05)."""

    osm_id: int | None = None
    surface: str | None = None
    length_m: float | None = None
    scored: bool = False                      # false: not eligible, not yet analysed, or no terrain data
    elevation_m: float | None = None
    slope_pct: float | None = None
    river_dist_m: float | None = None
    factor_scores: FactorBreakdown | None = None
    weights: FactorBreakdown | None = None
    data_flags: list[str] = []
    tier_thresholds: dict[str, float] | None = None   # lower bounds used to classify this result
    config_version: int | None = None
    model_version: str | None = None
    run_id: int | None = None
    computed_at: datetime | None = None       # analysis date
    terrain_samples: int | None = None


class GeoJSONFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any]


class GeoJSONFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[GeoJSONFeature]
    number_returned: int = 0
    truncated: bool = False                   # true: more roads matched than ``limit`` allowed


class TierLength(BaseModel):
    tier: Tier
    segments: int = 0
    length_km: float


class RoadStatsResponse(BaseModel):
    total_length_km: float
    total_segments: int = 0
    by_tier: list[TierLength]
    unscored_segments: int = 0
    unscored_length_km: float = 0.0
