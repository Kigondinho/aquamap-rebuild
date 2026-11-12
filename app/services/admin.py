"""Admin operations: layer list, scoring configuration, pipeline status. Implemented in a later phase."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.errors import NotImplementedYet
from app.schemas.admin import LayerInfo, PipelineStatus, ScoringConfigModel
from app.schemas.meta import MetaConfigResponse


def list_layers(db: Session) -> list[LayerInfo]:
    raise NotImplementedYet("layer listing")


def get_active_config(db: Session) -> ScoringConfigModel:
    raise NotImplementedYet("reading the active scoring configuration")


def save_config(db: Session, config: ScoringConfigModel, created_by: str) -> ScoringConfigModel:
    """Validate and store a new configuration version, then mark it active."""
    raise NotImplementedYet("saving scoring configuration")


def get_pipeline_status(db: Session) -> PipelineStatus:
    raise NotImplementedYet("pipeline status")


def get_public_meta(db: Session) -> MetaConfigResponse:
    """Tier definitions and weights for the frontend legend and popups."""
    raise NotImplementedYet("meta configuration")
