-- AquaMap database initialisation, step 3: indexes.

-- Spatial (GiST) indexes: bounding-box, intersection and nearest-neighbour queries.
CREATE INDEX IF NOT EXISTS road_segments_geom_gist     ON road_segments     USING GIST (geom);
CREATE INDEX IF NOT EXISTS river_centerlines_geom_gist ON river_centerlines USING GIST (geom);

-- Conventional indexes for attributes the API filters and joins on.
CREATE INDEX IF NOT EXISTS road_segments_class_idx ON road_segments (highway_type);
CREATE INDEX IF NOT EXISTS road_segments_name_idx  ON road_segments (lower(name) text_pattern_ops);
CREATE INDEX IF NOT EXISTS road_segments_osm_idx   ON road_segments (osm_id);
CREATE INDEX IF NOT EXISTS road_segments_layer_idx ON road_segments (layer_id);

CREATE INDEX IF NOT EXISTS river_centerlines_layer_idx    ON river_centerlines (layer_id);
CREATE INDEX IF NOT EXISTS river_centerlines_waterway_idx ON river_centerlines (waterway);

CREATE INDEX IF NOT EXISTS road_vulnerability_tier_idx   ON road_vulnerability (risk_category);
CREATE INDEX IF NOT EXISTS road_vulnerability_ivi_idx    ON road_vulnerability (vulnerability_index);
CREATE INDEX IF NOT EXISTS road_vulnerability_config_idx ON road_vulnerability (config_version);
CREATE INDEX IF NOT EXISTS road_vulnerability_run_idx    ON road_vulnerability (run_id);

CREATE INDEX IF NOT EXISTS auth_otps_user_idx      ON auth_otps (user_id);
CREATE INDEX IF NOT EXISTS pipeline_runs_start_idx ON pipeline_runs (started_at DESC);

-- At most one active scoring configuration at any time.
CREATE UNIQUE INDEX IF NOT EXISTS scoring_config_one_active ON scoring_config (is_active) WHERE is_active;

-- Phase 6: natural keys that make ingestion repeatable (ON CONFLICT targets). Rows without an osm_id
-- (hand-made or test rows) are not constrained, because NULLs never conflict.
CREATE UNIQUE INDEX IF NOT EXISTS road_segments_source_key     ON road_segments     (osm_element, osm_id, part_no);
CREATE UNIQUE INDEX IF NOT EXISTS river_centerlines_source_key ON river_centerlines (osm_element, osm_id, part_no);
CREATE UNIQUE INDEX IF NOT EXISTS spatial_layers_name_type_key ON spatial_layers (layer_name, layer_type);
