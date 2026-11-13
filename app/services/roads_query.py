"""Read-only spatial queries over roads and their stored vulnerability results.

Rules that every query here follows:

* Bound parameters only; no value from a request is ever put into the SQL text.
* Scores, categories and factor values are returned exactly as stored in ``road_vulnerability``
  (joined on ``road_id``); nothing is recomputed, rounded or re-classified on the way out.
* Geometry is stored in EPSG:32737 and returned as GeoJSON in EPSG:4326. Request coordinates arrive in
  EPSG:4326 and are transformed ONCE to EPSG:32737, so the GiST index on ``road_segments.geom`` is used
  and distances are in metres. Geometry is only simplified when the caller passes ``zoom``.
* Filter semantics: categories and road classes match any of the given values; ``min_ivi`` and
  ``max_ivi`` are both inclusive. Any filter on category or IVI leaves out roads without an index.
* Every list is paginated with ``limit`` and ``offset`` over a fixed, repeatable order, and reports
  the total number of matches, so a client can page through a large result without ever receiving it
  in one response.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.errors import InvalidRequestError, NotFoundError
from app.schemas.common import Pagination
from app.schemas.roads import (
    FactorBreakdown,
    GeoJSONFeature,
    GeoJSONFeatureCollection,
    RoadDetailProperties,
    RoadStatsResponse,
    RoadSummaryProperties,
    TierLength,
)
from core.config_defaults import API_SRID, NAIROBI_BBOX, SCORE_MAX, SCORE_MIN, STORAGE_SRID, TIER_NAMES
from core.geo import BBox

COORD_DECIMALS = 6          # about 0.1 m at the equator
BBOX_MARGIN_DEG = 0.05      # a map view may extend a little beyond the county envelope
MAX_RADIUS_M = 2000.0
SEARCH_MIN_CHARS = 2

_FROM = "FROM road_segments r LEFT JOIN road_vulnerability v ON v.road_id = r.road_id"
_SUMMARY_COLS = ("r.road_id AS id, r.name, r.highway_type, v.risk_category, "
                 "v.vulnerability_index::float8 AS vulnerability_index")
_POINT_SQL = f"ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), {API_SRID}), {STORAGE_SRID})"
_ENVELOPE_SQL = f"ST_Transform(ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, {API_SRID}), {STORAGE_SRID})"
# Highest index first, roads without an index last; road_id makes the order (and so LIMIT) repeatable.
_ORDER_BY_RISK = "ORDER BY v.vulnerability_index DESC NULLS LAST, r.road_id"


# ---------------------------------------------------------------------------- request parsing
def parse_bbox(raw: str) -> BBox:
    """Parse ``minLon,minLat,maxLon,maxLat`` (EPSG:4326); reject malformed boxes and boxes outside the county."""
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise InvalidRequestError("bbox must be four numbers: minLon,minLat,maxLon,maxLat.")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError as exc:
        raise InvalidRequestError("bbox must contain only numbers.") from exc
    if not all(math.isfinite(v) for v in (min_lon, min_lat, max_lon, max_lat)):
        raise InvalidRequestError("bbox must contain finite numbers.")
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180 and -90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise InvalidRequestError("bbox coordinates must be longitude/latitude in degrees.")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise InvalidRequestError("bbox must have minLon < maxLon and minLat < maxLat.")
    west, south, east, north = NAIROBI_BBOX
    m = BBOX_MARGIN_DEG
    if max_lon < west - m or min_lon > east + m or max_lat < south - m or min_lat > north + m:
        raise InvalidRequestError("bbox does not overlap Nairobi County.")
    # Clip to the service area: nothing is stored outside it, and it bounds the work a request can cause.
    return BBox(max(min_lon, west - m), max(min_lat, south - m), min(max_lon, east + m), min(max_lat, north + m))


def _check_ivi_range(min_ivi: float | None, max_ivi: float | None) -> None:
    for name, value in (("min_ivi", min_ivi), ("max_ivi", max_ivi)):
        if value is not None and not (math.isfinite(value) and SCORE_MIN <= value <= SCORE_MAX):
            raise InvalidRequestError(f"{name} must be between {SCORE_MIN:g} and {SCORE_MAX:g}.")
    if min_ivi is not None and max_ivi is not None and min_ivi > max_ivi:
        raise InvalidRequestError("min_ivi must not be greater than max_ivi.")


def _filters(tiers: Sequence[str] | None, highways: Sequence[str] | None, min_ivi: float | None,
             max_ivi: float | None) -> tuple[list[str], dict[str, Any]]:
    """WHERE fragments and their parameters for the attribute filters shared by all list queries."""
    _check_ivi_range(min_ivi, max_ivi)
    where: list[str] = []
    params: dict[str, Any] = {}
    if tiers:
        unknown = sorted(set(tiers) - set(TIER_NAMES))
        if unknown:
            raise InvalidRequestError(f"Unknown category {unknown}; use {list(TIER_NAMES)}.")
        where.append("v.risk_category = ANY(:tiers)")
        params["tiers"] = sorted(set(tiers))
    if highways:
        cleaned = sorted({h.strip() for h in highways if h and h.strip()})
        if cleaned:
            where.append("r.highway_type = ANY(:highways)")
            params["highways"] = cleaned
    if min_ivi is not None:
        where.append("v.vulnerability_index >= :min_ivi")
        params["min_ivi"] = min_ivi
    if max_ivi is not None:
        where.append("v.vulnerability_index <= :max_ivi")
        params["max_ivi"] = max_ivi
    return where, params


def simplify_tolerance_m(zoom: int | None) -> float:
    """Simplification tolerance in metres for a map zoom level; 0 = exact stored geometry."""
    if zoom is None or zoom >= 15:
        return 0.0
    if zoom >= 13:
        return 5.0
    if zoom >= 11:
        return 20.0
    return 60.0


def _geometry_sql(tolerance: float) -> str:
    geom = "ST_SimplifyPreserveTopology(r.geom, :tolerance)" if tolerance > 0 else "r.geom"
    return f"ST_AsGeoJSON(ST_Transform({geom}, {API_SRID}), {COORD_DECIMALS}) AS geometry"


def _where(fragments: list[str]) -> str:
    return ("WHERE " + " AND ".join(fragments)) if fragments else ""


def _summary_features(rows: Sequence[Any], extra: tuple[str, ...] = ()) -> list[GeoJSONFeature]:
    features = []
    for row in rows:
        m = row._mapping
        props = RoadSummaryProperties(id=m["id"], name=m["name"], highway_type=m["highway_type"],
                                      risk_category=m["risk_category"],
                                      vulnerability_index=m["vulnerability_index"]).model_dump()
        for key in extra:
            props[key] = m[key]
        features.append(GeoJSONFeature(geometry=json.loads(m["geometry"]), properties=props))
    return features


def _check_page(limit: int, offset: int) -> None:
    if limit < 1:
        raise InvalidRequestError("limit must be at least 1.")
    if offset < 0:
        raise InvalidRequestError("offset must not be negative.")


def _page(features: list[GeoJSONFeature], rows: Sequence[Any], limit: int, offset: int,
          count_all: Any) -> GeoJSONFeatureCollection:
    """Wrap one page. Each row carries ``total_count`` (a window count over all matches); a page beyond
    the end has no rows, so the total is then counted with ``count_all()``."""
    total = int(rows[0]._mapping["total_count"]) if rows else (int(count_all()) if offset > 0 else 0)
    has_more = offset + len(features) < total
    page = Pagination(limit=limit, offset=offset, returned=len(features), total=total, has_more=has_more,
                      next_offset=offset + len(features) if has_more else None)
    return GeoJSONFeatureCollection(features=features, pagination=page, number_returned=len(features), truncated=has_more)


# ---------------------------------------------------------------------------- list: bounding box
def query_roads(
    db: Session,
    bbox: BBox,
    tiers: Sequence[str] | None = None,
    highways: Sequence[str] | None = None,
    min_ivi: float | None = None,
    limit: int = 1000,
    zoom: int | None = None,
    max_ivi: float | None = None,
    offset: int = 0,
) -> GeoJSONFeatureCollection:
    """Roads intersecting ``bbox``, filtered, with light properties for drawing.

    Ordered by index, highest first, so the first page holds the most vulnerable roads of the area.
    """
    _check_page(limit, offset)
    where, params = _filters(tiers, highways, min_ivi, max_ivi)
    tolerance = simplify_tolerance_m(zoom)
    # `&&` is the index-assisted bounding-box test; ST_Intersects then removes lines whose box overlaps
    # the envelope although the line itself does not.
    where = [f"r.geom && {_ENVELOPE_SQL}", f"ST_Intersects(r.geom, {_ENVELOPE_SQL})", *where]
    params.update(minx=bbox.min_lon, miny=bbox.min_lat, maxx=bbox.max_lon, maxy=bbox.max_lat)
    count_params = dict(params)
    params.update(limit=limit, offset=offset)
    if tolerance > 0:
        params["tolerance"] = tolerance
    rows = db.execute(text(f"SELECT {_SUMMARY_COLS}, {_geometry_sql(tolerance)}, count(*) OVER() AS total_count "
                           f"{_FROM} {_where(where)} {_ORDER_BY_RISK} LIMIT :limit OFFSET :offset"), params).all()
    return _page(_summary_features(rows), rows, limit, offset,
                 lambda: db.execute(text(f"SELECT count(*) {_FROM} {_where(where)}"), count_params).scalar_one())


# ---------------------------------------------------------------------------- list: radius
def nearby_roads(
    db: Session,
    lat: float,
    lon: float,
    radius_m: float = 500.0,
    limit: int = 20,
    tiers: Sequence[str] | None = None,
    highways: Sequence[str] | None = None,
    min_ivi: float | None = None,
    max_ivi: float | None = None,
    offset: int = 0,
) -> GeoJSONFeatureCollection:
    """Roads with any part within ``radius_m`` metres of a point, nearest first.

    Distance is measured in EPSG:32737 from the point to the nearest part of each line and returned
    as ``distance_m``. A road exactly ``radius_m`` away is included.
    """
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise InvalidRequestError("lat and lon must be degrees in EPSG:4326.")
    if not (math.isfinite(radius_m) and 0 < radius_m <= MAX_RADIUS_M):
        raise InvalidRequestError(f"radius_m must be greater than 0 and at most {MAX_RADIUS_M:g}.")
    _check_page(limit, offset)
    where, params = _filters(tiers, highways, min_ivi, max_ivi)
    params.update(lon=lon, lat=lat, radius=radius_m)
    count_params = dict(params)
    params.update(limit=limit, offset=offset)
    within = _where(["ST_DWithin(r.geom, p.geom, :radius)", *where])
    point = f"WITH p AS (SELECT {_POINT_SQL} AS geom) "
    # The point is transformed once in a CTE; ST_DWithin uses the GiST index (it expands the point by
    # the radius and compares bounding boxes first).
    rows = db.execute(text(
        f"{point}SELECT {_SUMMARY_COLS}, ST_Distance(r.geom, p.geom) AS distance_m, {_geometry_sql(0.0)}, "
        f"count(*) OVER() AS total_count {_FROM} CROSS JOIN p {within} "
        "ORDER BY ST_Distance(r.geom, p.geom), r.road_id LIMIT :limit OFFSET :offset"), params).all()
    return _page(_summary_features(rows, extra=("distance_m",)), rows, limit, offset,
                 lambda: db.execute(text(f"{point}SELECT count(*) {_FROM} CROSS JOIN p {within}"), count_params).scalar_one())


# ---------------------------------------------------------------------------- search by name
def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_roads(db: Session, q: str, limit: int = 10, offset: int = 0) -> GeoJSONFeatureCollection:
    """Case-insensitive name search, one result per distinct name.

    Names that start with the text come first, then names that contain it. Each result is all segments
    carrying that name: their combined geometry, a ``bbox`` to zoom to, the number of segments, their
    total length, and the highest stored index among them with the category stored on that same segment.
    """
    needle = " ".join(q.split()).lower()
    if len(needle) < SEARCH_MIN_CHARS:
        raise InvalidRequestError(f"Search text must have at least {SEARCH_MIN_CHARS} characters.")
    _check_page(limit, offset)
    escaped = _like_escape(needle)
    rows = db.execute(text(
        "WITH hits AS ("
        "  SELECT r.road_id, r.name, r.highway_type, r.length_m, r.geom, v.vulnerability_index, v.risk_category, "
        "         (lower(r.name) LIKE :prefix) AS is_prefix "
        f"  {_FROM} WHERE lower(r.name) LIKE :contains"
        "), named AS ("
        "  SELECT lower(name) AS key, min(name) AS name, bool_or(is_prefix) AS is_prefix, count(*) AS segments, "
        "         sum(length_m) AS length_m, "
        "         (array_agg(road_id ORDER BY vulnerability_index DESC NULLS LAST, road_id))[1] AS id, "
        "         (array_agg(highway_type ORDER BY vulnerability_index DESC NULLS LAST, road_id))[1] AS highway_type, "
        "         (array_agg(vulnerability_index ORDER BY vulnerability_index DESC NULLS LAST, road_id))[1] AS vulnerability_index, "
        "         (array_agg(risk_category ORDER BY vulnerability_index DESC NULLS LAST, road_id))[1] AS risk_category, "
        "         count(vulnerability_index) AS segments_scored, "
        f"         ST_Transform(ST_Collect(geom), {API_SRID}) AS geom "
        "  FROM hits GROUP BY lower(name)"
        ") SELECT id, name, highway_type, risk_category, vulnerability_index::float8 AS vulnerability_index, "
        "         segments, segments_scored, length_m, "
        f"         ST_AsGeoJSON(geom, {COORD_DECIMALS}) AS geometry, "
        "         ARRAY[ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)] AS bbox, "
        "         count(*) OVER() AS total_count "
        "  FROM named ORDER BY is_prefix DESC, key LIMIT :limit OFFSET :offset"),
        {"prefix": escaped + "%", "contains": "%" + escaped + "%", "limit": limit, "offset": offset}).all()
    features = []
    for row in rows:
        m = row._mapping
        props = RoadSummaryProperties(id=m["id"], name=m["name"], highway_type=m["highway_type"],
                                      risk_category=m["risk_category"],
                                      vulnerability_index=m["vulnerability_index"]).model_dump()
        props.update(segments=m["segments"], segments_scored=m["segments_scored"],
                     length_m=float(m["length_m"]), bbox=[round(float(c), COORD_DECIMALS) for c in m["bbox"]])
        features.append(GeoJSONFeature(geometry=json.loads(m["geometry"]), properties=props))
    return _page(features, rows, limit, offset, lambda: db.execute(text(
        "SELECT count(DISTINCT lower(name)) FROM road_segments r WHERE lower(r.name) LIKE :contains"),
        {"contains": "%" + escaped + "%"}).scalar_one())


# ---------------------------------------------------------------------------- one road
def get_road(db: Session, road_id: int) -> GeoJSONFeature:
    """One road with every stored result value; raise ``NotFoundError`` if there is no such road."""
    row = db.execute(text(
        "SELECT r.road_id AS id, r.osm_id, r.name, r.highway_type, r.surface, r.length_m, "
        "       v.road_id IS NOT NULL AS has_result, v.risk_category, v.vulnerability_index::float8 AS vulnerability_index, "
        "       v.elevation_m, v.slope_pct, v.river_dist_m, v.score_elevation, v.score_slope, v.score_river, "
        "       v.score_surface, v.data_flags, v.config_version, v.model_version, v.computed_at, v.terrain_samples, "
        "       v.run_id, c.weights, c.tier_thresholds, "
        f"       ST_AsGeoJSON(ST_Transform(r.geom, {API_SRID}), {COORD_DECIMALS}) AS geometry "
        f"{_FROM} LEFT JOIN scoring_config c ON c.config_version = v.config_version WHERE r.road_id = :id"),
        {"id": road_id}).one_or_none()
    if row is None:
        raise NotFoundError("Road not found.")
    m = row._mapping
    scores = weights = None
    if m["has_result"]:
        scores = FactorBreakdown(elevation=m["score_elevation"], slope=m["score_slope"], river=m["score_river"],
                                 surface=m["score_surface"])
        w = m["weights"] or {}
        weights = FactorBreakdown(elevation=w.get("elevation"), slope=w.get("slope"), river=w.get("river"),
                                  surface=w.get("surface"))
    props = RoadDetailProperties(
        id=m["id"], osm_id=m["osm_id"], name=m["name"], highway_type=m["highway_type"], surface=m["surface"],
        length_m=m["length_m"], scored=m["vulnerability_index"] is not None, risk_category=m["risk_category"],
        vulnerability_index=m["vulnerability_index"], elevation_m=m["elevation_m"], slope_pct=m["slope_pct"],
        river_dist_m=m["river_dist_m"], factor_scores=scores, weights=weights, data_flags=list(m["data_flags"] or []),
        config_version=m["config_version"], model_version=m["model_version"], computed_at=m["computed_at"],
        terrain_samples=m["terrain_samples"], run_id=m["run_id"], tier_thresholds=m["tier_thresholds"])
    return GeoJSONFeature(geometry=json.loads(m["geometry"]), properties=props.model_dump(mode="json"))


# ---------------------------------------------------------------------------- totals
def road_stats(
    db: Session,
    tiers: Sequence[str] | None = None,
    highways: Sequence[str] | None = None,
    bbox: BBox | None = None,
    min_ivi: float | None = None,
    max_ivi: float | None = None,
) -> RoadStatsResponse:
    """Segment count and length in total, per category and without an index, for the given filters.

    A road counts with its whole length if any part of it is inside ``bbox``.
    """
    where, params = _filters(tiers, highways, min_ivi, max_ivi)
    if bbox is not None:
        where = [f"r.geom && {_ENVELOPE_SQL}", f"ST_Intersects(r.geom, {_ENVELOPE_SQL})", *where]
        params.update(minx=bbox.min_lon, miny=bbox.min_lat, maxx=bbox.max_lon, maxy=bbox.max_lat)
    rows = db.execute(text(f"SELECT v.risk_category AS tier, count(*) AS segments, coalesce(sum(r.length_m), 0) AS length_m "
                           f"{_FROM} {_where(where)} GROUP BY v.risk_category"), params).all()
    by = {r.tier: (int(r.segments), float(r.length_m)) for r in rows}
    unscored = by.get(None, (0, 0.0))
    return RoadStatsResponse(
        total_length_km=round(sum(v[1] for v in by.values()) / 1000, 3),
        total_segments=sum(v[0] for v in by.values()),
        by_tier=[TierLength(tier=t, segments=by.get(t, (0, 0.0))[0], length_km=round(by.get(t, (0, 0.0))[1] / 1000, 3))
                 for t in TIER_NAMES],
        unscored_segments=unscored[0], unscored_length_km=round(unscored[1] / 1000, 3))
