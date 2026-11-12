"""Small geographic value types shared by the API and the pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from core.config_defaults import NAIROBI_BBOX


@dataclass(frozen=True, slots=True)
class BBox:
    """Axis-aligned bounding box in EPSG:4326 (longitude/latitude, degrees)."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


NAIROBI = BBox(*NAIROBI_BBOX)
