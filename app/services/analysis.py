"""Metadata of the stored vulnerability analysis: model, configuration, run, coverage and data sources."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.analysis import (
    AnalysisCoverage,
    AnalysisMetadataResponse,
    FactorDefinition,
    LayerSummary,
    RunInfo,
)
from app.schemas.meta import TierDefinition
from app.services.admin import TIER_BOUNDARY_RULE
from core import ivi
from core.config_defaults import API_SRID, SCORE_MAX, SCORE_MIN, STORAGE_SRID, SURFACE_UNKNOWN_KEY, TIER_LABELS

FORMULA = "IVI = (WE x E) + (WS x S) + (WD x D) + (WM x M)"
ATTRIBUTION = ["© OpenStreetMap contributors (ODbL)", "SRTM terrain data courtesy of the U.S. Geological Survey"]
_MEASURED = {
    "elevation": ("Mean DEM elevation sampled along the road", "m"),
    "slope": ("Mean terrain slope sampled along the road", "%"),
    "river": ("Distance from the road to the nearest waterway", "m"),
    "surface": ("OpenStreetMap surface tag", None),
}


def get_analysis_metadata(db: Session) -> AnalysisMetadataResponse:
    """Describe the results currently stored. Before any run it returns the documented defaults with ``analysed=false``."""
    stored = db.execute(text(
        "SELECT count(*) AS analysed, count(vulnerability_index) AS with_index, max(computed_at) AS analysis_date, "
        "max(model_version) AS model_version, max(config_version) AS config_version, max(run_id) AS run_id "
        "FROM road_vulnerability")).mappings().one()
    roads_total = int(db.execute(text("SELECT count(*) FROM road_segments")).scalar_one())
    flags = {r[0]: int(r[1]) for r in db.execute(text(
        "SELECT flag, count(*) FROM road_vulnerability, unnest(data_flags) AS flag GROUP BY flag ORDER BY flag"))}

    # The configuration that produced the stored results; before any run, the active one; else the defaults.
    config, config_version = ivi.ScoringConfig(), None
    row = db.execute(text(
        "SELECT config_version, weights, breakpoints, surface_map, tier_thresholds FROM scoring_config "
        "WHERE config_version = :c OR (CAST(:c AS integer) IS NULL AND is_active) LIMIT 1"),
        {"c": stored["config_version"]}).one_or_none()
    if row is not None:
        try:
            config, config_version = ivi.ScoringConfig.from_storage(row[1], row[2], row[3], row[4]), int(row[0])
        except (ValueError, KeyError, TypeError):
            config, config_version = ivi.ScoringConfig(), None

    run = None
    run_row = db.execute(text(
        "SELECT run_id, status, started_at, finished_at, segments_scored, segments_flagged FROM pipeline_runs "
        "WHERE run_id = :r OR (CAST(:r AS integer) IS NULL AND TRUE) ORDER BY run_id DESC LIMIT 1"),
        {"r": stored["run_id"]}).mappings().one_or_none()
    if run_row is not None:
        run = RunInfo(**run_row)

    factors = []
    for name in ivi.FACTORS:
        measured, unit = _MEASURED[name]
        spec = config.breakpoints.get(name)
        factors.append(FactorDefinition(
            factor=name, measured_as=measured, unit=unit, weight=config.weights[name],
            upper_bounds=list(spec["edges"]) if spec else None, scores=list(spec["scores"]) if spec else None))
    layers = [LayerSummary(**r) for r in db.execute(text(
        "SELECT layer_name, layer_type, source, license, feature_count, uploaded_at AS loaded_at FROM spatial_layers "
        "ORDER BY layer_type, layer_name")).mappings()]
    analysed, with_index = int(stored["analysed"]), int(stored["with_index"])
    return AnalysisMetadataResponse(
        analysed=analysed > 0, index_name="Infrastructure Vulnerability Index (IVI)", formula=FORMULA,
        scale_min=SCORE_MIN, scale_max=SCORE_MAX, interpretation=ivi.INTERPRETATION_NOTE,
        model_version=stored["model_version"], config_version=config_version, analysis_date=stored["analysis_date"],
        factors=factors,
        surface_scores={**dict(sorted(config.surface_scores.items())), SURFACE_UNKNOWN_KEY: config.surface_unknown_score},
        tiers=[TierDefinition(tier=n, lower_bound=lo, upper_bound=hi, plain_label=TIER_LABELS[n])
               for n, lo, hi in ivi.tier_bounds(config)],
        boundary_rule=TIER_BOUNDARY_RULE, last_run=run,
        coverage=AnalysisCoverage(roads_total=roads_total, roads_analysed=analysed, roads_with_index=with_index,
                                  roads_without_index=analysed - with_index, roads_not_analysed=roads_total - analysed,
                                  flags=flags),
        layers=layers, crs_storage=f"EPSG:{STORAGE_SRID}", crs_api=f"EPSG:{API_SRID}", attribution=ATTRIBUTION)
