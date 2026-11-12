"""Read-only spatial queries over ``road_segments`` (parameterised SQL only). Implemented in a later phase."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.errors import NotImplementedYet
from app.schemas.roads import GeoJSONFeature, GeoJSONFeatureCollection, RoadStatsResponse
from core.geo import BBox


def parse_bbox(raw: str) -> BBox:
    """Parse ``minLon,minLat,maxLon,maxLat``; reject malformed, out-of-county or oversized boxes."""
    raise NotImplementedYet("bounding-box parsing")


def query_roads(
    db: Session,
    bbox: BBox,
    tiers: Sequence[str] | None = None,
    highways: Sequence[str] | None = None,
    min_ivi: float | None = None,
    limit: int = 5000,
    zoom: int | None = None,
) -> GeoJSONFeatureCollection:
    """Roads intersecting ``bbox``, filtered, with light properties for drawing."""
    raise NotImplementedYet("road bbox query")


def get_road(db: Session, road_id: int) -> GeoJSONFeature:
    """One road with full detail; raise ``NotFoundError`` if missing."""
    raise NotImplementedYet("road detail query")


def search_roads(db: Session, q: str, limit: int = 10) -> GeoJSONFeatureCollection:
    """Case-insensitive name search, one result per distinct name."""
    raise NotImplementedYet("road name search")


def road_stats(
    db: Session,
    tiers: Sequence[str] | None = None,
    highways: Sequence[str] | None = None,
    bbox: BBox | None = None,
) -> RoadStatsResponse:
    """Total length and length per tier for the current filters."""
    raise NotImplementedYet("road statistics")


def nearby_roads(
    db: Session, lat: float, lon: float, radius_m: float = 500.0, limit: int = 20
) -> GeoJSONFeatureCollection:
    """Nearest road segments to a point (optional feature)."""
    raise NotImplementedYet("nearby roads")
