from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import StringConstraints
from fastapi.responses import StreamingResponse

from app.deps import CurrentUserDep, DbSession
from app.schemas.common import Tier, error_responses
from app.schemas.roads import GeoJSONFeature, GeoJSONFeatureCollection, RoadStatsResponse
from app.services import export as export_service
from app.services import roads_query

router = APIRouter(prefix="/roads", tags=["Roads"], responses=error_responses(401, 422, 503))

OffsetParam = Annotated[int, Query(ge=0, le=1_000_000, description="Items to skip; use pagination.next_offset for the next page")]

BBoxParam = Annotated[
    str, Query(description="minLon,minLat,maxLon,maxLat in EPSG:4326", examples=["36.78,-1.31,36.86,-1.26"])
]
TierParam = Annotated[list[Tier] | None, Query(description="Category; repeat to select several", max_length=4)]
HighwayParam = Annotated[
    list[Annotated[str, StringConstraints(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_:\-]+$")]] | None,
    Query(description="OSM highway class, e.g. primary; repeat to select several", max_length=30)]
MinIviParam = Annotated[float | None, Query(ge=1.0, le=5.0, description="Minimum IVI, inclusive (1-5)")]
MaxIviParam = Annotated[float | None, Query(ge=1.0, le=5.0, description="Maximum IVI, inclusive (1-5)")]


@router.get("", response_model=GeoJSONFeatureCollection, summary="Roads in a map area")
def list_roads(
    _user: CurrentUserDep,
    db: DbSession,
    bbox: BBoxParam,
    tier: TierParam = None,
    highway: HighwayParam = None,
    min_ivi: MinIviParam = None,
    max_ivi: MaxIviParam = None,
    limit: Annotated[int, Query(ge=1, le=5000, description="Page size")] = 1000,
    offset: OffsetParam = 0,
    zoom: Annotated[int | None, Query(ge=0, le=22, description="Map zoom; below 15 the geometry is simplified")] = None,
) -> GeoJSONFeatureCollection:
    """Road retrieval and filtering. Highest index first; at most 5,000 roads per page."""
    return roads_query.query_roads(
        db, roads_query.parse_bbox(bbox), tier, highway, min_ivi, limit, zoom, max_ivi, offset
    )


@router.get("/search", response_model=GeoJSONFeatureCollection, summary="Search roads by name")
def search_roads(
    _user: CurrentUserDep,
    db: DbSession,
    q: Annotated[str, Query(min_length=2, max_length=100, description="Part of a road name, at least 2 characters")],
    limit: Annotated[int, Query(ge=1, le=50, description="Page size")] = 10,
    offset: OffsetParam = 0,
) -> GeoJSONFeatureCollection:
    """One result per road name: names starting with the text first, then names containing it."""
    return roads_query.search_roads(db, q, limit, offset)


@router.get("/summary", response_model=RoadStatsResponse, summary="Vulnerability statistics: segments and length per category")
def road_summary(
    _user: CurrentUserDep,
    db: DbSession,
    bbox: Annotated[str | None, Query(description="Optional minLon,minLat,maxLon,maxLat")] = None,
    tier: TierParam = None,
    highway: HighwayParam = None,
    min_ivi: MinIviParam = None,
    max_ivi: MaxIviParam = None,
) -> RoadStatsResponse:
    parsed = roads_query.parse_bbox(bbox) if bbox else None
    return roads_query.road_stats(db, tier, highway, parsed, min_ivi, max_ivi)


@router.get("/export", summary="Download filtered roads as GeoJSON (not built)", responses=error_responses(413, 501))
def export_roads(
    _user: CurrentUserDep,
    db: DbSession,
    bbox: Annotated[str | None, Query(description="Optional minLon,minLat,maxLon,maxLat")] = None,
    tier: TierParam = None,
    highway: HighwayParam = None,
    min_ivi: MinIviParam = None,
) -> StreamingResponse:
    parsed = roads_query.parse_bbox(bbox) if bbox else None
    chunks = export_service.stream_geojson_export(db, parsed, tier, highway, min_ivi)
    return StreamingResponse(
        chunks,
        media_type="application/geo+json",
        headers={"Content-Disposition": 'attachment; filename="aquamap_roads.geojson"'},
    )


@router.get("/nearby", response_model=GeoJSONFeatureCollection, summary="Roads near a point")
def nearby_roads(
    _user: CurrentUserDep,
    db: DbSession,
    lat: Annotated[float, Query(ge=-90.0, le=90.0)],
    lon: Annotated[float, Query(ge=-180.0, le=180.0)],
    radius_m: Annotated[float, Query(gt=0.0, le=2000.0, description="Radius in metres")] = 500.0,
    limit: Annotated[int, Query(ge=1, le=500, description="Page size")] = 20,
    offset: OffsetParam = 0,
    tier: TierParam = None,
    highway: HighwayParam = None,
    min_ivi: MinIviParam = None,
    max_ivi: MaxIviParam = None,
) -> GeoJSONFeatureCollection:
    """Roads with any part within `radius_m` metres of the point, nearest first, with `distance_m`."""
    return roads_query.nearby_roads(db, lat, lon, radius_m, limit, tier, highway, min_ivi, max_ivi, offset)


@router.get("/{road_id}", response_model=GeoJSONFeature, summary="One road with full detail",
            responses=error_responses(404))
def get_road(_user: CurrentUserDep, db: DbSession, road_id: Annotated[int, Path(ge=1)]) -> GeoJSONFeature:
    return roads_query.get_road(db, road_id)
