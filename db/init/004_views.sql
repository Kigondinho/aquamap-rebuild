-- AquaMap database initialisation, step 4: views.

-- Roads joined with their current vulnerability result. Roads that have not been scored yet
-- appear with NULL result columns. vulnerability_index is a relative index on a 1..5 scale
-- (5 = most vulnerable), not a flood probability. Geometry stays in EPSG:32737; the API reprojects on output.
CREATE OR REPLACE VIEW v_road_risk AS
SELECT
    r.road_id,
    r.osm_id,
    r.name,
    r.highway_type,
    r.surface,
    r.length_m,
    r.geom,
    v.elevation_m,
    v.slope_pct,
    v.river_dist_m,
    v.score_elevation,
    v.score_river,
    v.score_slope,
    v.score_surface,
    v.vulnerability_index,
    v.risk_category,
    v.data_flags,
    v.config_version,
    v.run_id,
    v.computed_at,
    v.model_version,
    v.terrain_samples
FROM road_segments r
LEFT JOIN road_vulnerability v ON v.road_id = r.road_id;
