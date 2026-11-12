from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class LayerInfo(BaseModel):
    layer_id: int
    layer_name: str
    layer_type: Literal["roads", "rivers", "dem"]
    source: str | None = None
    feature_count: int | None = None
    uploaded_at: datetime


class ScoringConfigModel(BaseModel):
    """Body of ``PUT /admin/config`` and of the active-config response."""

    weights: dict[str, float]
    breakpoints: dict[str, dict[str, list[float]]]
    surface_map: dict[str, float]
    tier_thresholds: dict[str, float]
    config_version: int | None = None


class PipelineStatus(BaseModel):
    run_id: int | None = None
    status: Literal["idle", "running", "succeeded", "failed"] = "idle"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    segments_scored: int | None = None
    segments_flagged: int | None = None
