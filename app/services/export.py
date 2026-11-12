"""GeoJSON download of filtered roads. Implemented in a later phase."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from sqlalchemy.orm import Session

from app.errors import NotImplementedYet
from core.geo import BBox


def stream_geojson_export(
    db: Session,
    bbox: BBox | None = None,
    tiers: Sequence[str] | None = None,
    highways: Sequence[str] | None = None,
    min_ivi: float | None = None,
) -> Iterator[bytes]:
    """Yield a GeoJSON FeatureCollection in chunks; raise ``PayloadTooLargeError`` above the cap."""
    raise NotImplementedYet("GeoJSON export")
