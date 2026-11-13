from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query
from fastapi.responses import StreamingResponse

from app.deps import CurrentUserDep, DbSession
from app.schemas.common import Tier
from app.schemas.roads import GeoJSONFeature, GeoJSONFeatureCollection, RoadStatsResponse
from app.services import export as export_service
from app.services import roads_query

router = APIRouter(prefix="/roads", tags=["Roads"])

BBoxParam = Annotated[
    str, Query(description="minLon,minLat,maxLon,maxLat in EPSG:4326", examples=["36.78,-1.31,36.86,-1.26"])
]
TierParam = Annotated[list[Tier] | None, Query(description="Repeat to select several tiers")]
HighwayParam = Annotated[list[str] | None, Query(description="OSM highway classes")]
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
    limit: Annotated[int, Query(ge=1, le=10000)] = 5000,
    zoom: Annotated[int | None, Query(ge=0, le=22, description="Map zoom; below 15 the geometry is simplified")] = None,
) -> GeoJSONFeatureCollection:
    return roads_query.query_roads(
        db, roads_query.parse_bbox(bbox), tier, highway, min_ivi, limit, zoom, max_ivi
    )


@router.get("/search", response_model=GeoJSONFeatureCollection, summary="Search roads by name")
def search_roads(
    _user: CurrentUserDep,
    db: DbSession,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> GeoJSONFeatureCollection:
    return roads_query.search_roads(db, q, limit)


@router.get("/summary", response_model=RoadStatsResponse, summary="Length totals per risk tier")
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


@router.get("/export", summary="Download filtered roads as GeoJSON")
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
    limit: Annotated[int, Query(ge=1, le=1000)] = 20,
    tier: TierParam = None,
    highway: HighwayParam = None,
    min_ivi: MinIviParam = None,
    max_ivi: MaxIviParam = None,
) -> GeoJSONFeatureCollection:
    return roads_query.nearby_roads(db, lat, lon, radius_m, limit, tier, highway, min_ivi, max_ivi)


@router.get("/{road_id}", response_model=GeoJSONFeature, summary="One road with full detail")
def get_road(_user: CurrentUserDep, db: DbSession, road_id: Annotated[int, Path(ge=1)]) -> GeoJSONFeature:
    return roads_query.get_road(db, road_id)
