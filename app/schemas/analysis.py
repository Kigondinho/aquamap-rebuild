from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.meta import TierDefinition


class FactorDefinition(BaseModel):
    """How one factor is measured, weighted and turned into a 1-5 class score."""

    factor: str
    measured_as: str
    unit: str | None = None
    weight: float
    upper_bounds: list[float] | None = Field(default=None, description="Ascending class boundaries; a value on a boundary belongs to the class above")
    scores: list[float] | None = Field(default=None, description="Class scores, one more than upper_bounds; first = lowest values")


class RunInfo(BaseModel):
    run_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    segments_scored: int | None = None
    segments_flagged: int | None = None


class LayerSummary(BaseModel):
    layer_name: str
    layer_type: str
    source: str | None = None
    license: str | None = None
    feature_count: int | None = None
    loaded_at: datetime


class AnalysisCoverage(BaseModel):
    roads_total: int
    roads_analysed: int = Field(description="Roads with a stored result (eligible classes)")
    roads_with_index: int
    roads_without_index: int = Field(description="Analysed, but no terrain data, so no index")
    roads_not_analysed: int = Field(description="Not eligible by road class, or loaded after the last run")
    flags: dict[str, int] = Field(description="Number of analysed roads carrying each data flag")


class AnalysisMetadataResponse(BaseModel):
    """What produced the stored vulnerability results, and how to read them."""

    analysed: bool = Field(description="False until the scoring pipeline has stored results")
    index_name: str
    formula: str
    scale_min: float
    scale_max: float
    interpretation: str = Field(description="What the index is not: read before using any score")
    model_version: str | None = None
    config_version: int | None = None
    analysis_date: datetime | None = None
    factors: list[FactorDefinition]
    surface_scores: dict[str, float]
    tiers: list[TierDefinition]
    boundary_rule: str
    last_run: RunInfo | None = None
    coverage: AnalysisCoverage
    layers: list[LayerSummary]
    crs_storage: str
    crs_api: str
    attribution: list[str]
