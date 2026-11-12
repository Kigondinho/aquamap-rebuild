# Phase 3 — Database design (PostgreSQL 16 + PostGIS 3.4)

This amends `phase1_requirements_and_design.md`, section 4.

## Decisions that changed

| Topic | Phase 1 | Phase 3 | Why |
|---|---|---|---|
| Stored CRS | EPSG:4326 | **EPSG:32737** (WGS 84 / UTM 37S) | Requested. Metric units in the database: `ST_Length`, `ST_Distance` and `ST_DWithin` return metres with no casting. Nairobi (about 1.3° S) is in the southern zone, so 32737 is correct; the legacy code used 32637 (37N). |
| Served CRS | 4326 | 4326 (unchanged) | The API returns `ST_AsGeoJSON(ST_Transform(geom, 4326))`. Bounding boxes arrive in lon/lat and are transformed once: `geom && ST_Transform(ST_MakeEnvelope(..., 4326), 32737)`. |
| Analysis results | Columns on `road_segments` | **Separate table `road_vulnerability`** | Geometry is loaded once; re-scoring never rewrites it. Each result records `config_version` and `run_id`. One current row per road (assumption A9). |
| Scoring metrics (`elevation_m`, `slope_pct`, `river_dist_m`) | On `road_segments` | On `road_vulnerability` | They are analysis outputs of a pipeline run. |

Trade-off: every GeoJSON response pays an `ST_Transform` per feature. At the sizes in scope (at most a few thousand features per viewport) this is small; if it ever matters, add a stored 4326 column.

## Tables

```
users ─┬─< auth_otps
       └─< scoring_config >─┬─< pipeline_runs
                            └─< road_vulnerability >── road_segments >── spatial_layers
                                                       river_centerlines >── spatial_layers
```

| Table | Purpose | Key constraints |
|---|---|---|
| `users` | Accounts | PK UUID; unique lower-case email; role in (`admin`,`user`); stores a password hash only |
| `auth_otps` | Email 2FA codes | FK users ON DELETE CASCADE; attempts ≥ 0 |
| `spatial_layers` | Data provenance | layer_type in (`roads`,`rivers`,`dem`) |
| `scoring_config` | Versioned weights, breakpoints, thresholds | unique partial index: at most one `is_active` |
| `pipeline_runs` | Run log | status in (`running`,`succeeded`,`failed`); finished ≥ started |
| `road_segments` | Road geometry and OSM attributes | `geometry(LineString, 32737)` NOT NULL; valid and non-empty; generated `length_m` |
| `river_centerlines` | River geometry | same geometry rules; generated `length_m` |
| `road_vulnerability` | Measured inputs, factor scores, IVI, tier, data flags | PK = FK road_id (ON DELETE CASCADE); IVI in 0–1; tier in the four names; IVI and tier both set or both null; scores in 0–1; slope and distance ≥ 0 |
| `v_road_risk` (view) | Roads LEFT JOIN results | unscored roads show NULL results |

## Indexes

- **GiST:** `road_segments_geom_gist`, `river_centerlines_geom_gist`.
- **B-tree:** road class, `lower(name)` (prefix search, `text_pattern_ops`), osm_id, layer_id; river layer and waterway; result tier, IVI, config_version, run_id; OTP user; run start time; unique active config.

## Initialisation

SQL in `db/init/` runs in name order: `001_extensions`, `002_schema`, `003_indexes`, `004_views`. All statements are idempotent.

- First start of an empty Docker volume: run automatically by the PostGIS image.
- Any time, without losing data: `python -m app.db_tools init` (one transaction; all or nothing).
- Inspect: `python -m app.db_tools check` reports PostGIS version, tables, geometry SRIDs and GiST indexes, and exits non-zero on any problem.
- If you already started a database from the Phase 2 scripts (EPSG:4326 columns), reset it: `docker compose down -v`, then start again.

## Connection management

`app/db.py`: one lazily created SQLAlchemy engine per process; pool size, overflow, timeout, recycle and connect timeout come from environment variables; `pool_pre_ping` recovers from dropped connections.

- `session_scope()` for writers: commit on success, roll back on any exception, always close.
- `get_db()` as the FastAPI dependency: one session per request, rolled back and closed afterwards; writers call `commit()` explicitly.
- Credentials: only from the environment (`POSTGRES_*`); Compose refuses to start without `POSTGRES_PASSWORD`; a test scans the repository for literal passwords.

## Production hardening (not done here)

The Docker image's `POSTGRES_USER` is a superuser. For a real deployment create a separate least-privilege application role (no superuser, no DDL) and use it for the web app; keep the owner role for migrations and the pipeline.
