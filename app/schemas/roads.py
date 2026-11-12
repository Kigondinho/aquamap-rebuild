from __future__ import annotations

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
    elevation: float | None = None
    river: float | None = None
    slope: float | None = None
    surface: float | None = None


class RoadDetailProperties(RoadSummaryProperties):
    """Full properties for the detail popup (explainability, NFR-05)."""

    surface: str | None = None
    length_m: float | None = None
    elevation_m: float | None = None
    slope_pct: float | None = None
    river_dist_m: float | None = None
    factor_scores: FactorBreakdown | None = None
    weights: FactorBreakdown | None = None
    data_flags: list[str] = []
    config_version: int | None = None


class GeoJSONFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any]


class GeoJSONFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[GeoJSONFeature]


class TierLength(BaseModel):
    tier: Tier
    length_km: float


class RoadStatsResponse(BaseModel):
    total_length_km: float
    by_tier: list[TierLength]
