"""Steps 6-7: apply ``core.ivi`` to every segment and persist scores and the run record."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Engine

from core.ivi import ScoringConfig

if TYPE_CHECKING:
    import geopandas as gpd


def score_segments(roads: "gpd.GeoDataFrame", config: ScoringConfig) -> "gpd.GeoDataFrame":
    """Add factor scores, ``vulnerability_index``, ``risk_category`` and ``data_flags``."""
    raise NotImplementedError("segment scoring")


def persist_scores(roads: "gpd.GeoDataFrame", engine: Engine, config_version: int) -> int:
    """Write scores inside one transaction and record a ``pipeline_runs`` row. Returns rows updated."""
    raise NotImplementedError("score persistence")
