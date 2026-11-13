"""Admin operations: layer list, scoring configuration, pipeline status. Implemented in a later phase."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.errors import ConflictError, NotImplementedYet
from app.schemas.admin import LayerInfo, PipelineStatus, ScoringConfigModel
from app.schemas.meta import MetaConfigResponse, TierDefinition
from core import ivi
from core.config_defaults import SCORE_MAX, SCORE_MIN, TIER_LABELS

TIER_BOUNDARY_RULE = (
    "A category covers lower_bound <= IVI < upper_bound; a value exactly on a bound belongs to the higher "
    "category. The IVI is stored with three decimals and classified from that stored value."
)


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
    """Tier definitions and weights of the active configuration, for the legend and popups.

    Read from ``scoring_config`` so the interface shows the same thresholds that classified the stored
    results. Before any configuration exists the documented defaults are returned with no version.
    """
    row = db.execute(text("SELECT config_version, weights, breakpoints, surface_map, tier_thresholds "
                          "FROM scoring_config WHERE is_active")).one_or_none()
    version: int | None = None
    config = ivi.ScoringConfig()
    if row is not None:
        try:
            config = ivi.ScoringConfig.from_storage(row[1], row[2], row[3], row[4])
            version = int(row[0])
        except (ValueError, KeyError, TypeError) as exc:
            raise ConflictError("The active scoring configuration is not valid; store a new one.") from exc
    return MetaConfigResponse(
        tiers=[TierDefinition(tier=name, lower_bound=lower, upper_bound=upper, plain_label=TIER_LABELS[name])
               for name, lower, upper in ivi.tier_bounds(config)],
        boundary_rule=TIER_BOUNDARY_RULE, scale_min=SCORE_MIN, scale_max=SCORE_MAX,
        weights=dict(config.weights), config_version=version, disclaimer=ivi.INTERPRETATION_NOTE)
