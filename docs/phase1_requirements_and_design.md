# AquaMap Rebuild: Phase 1 — Requirements Analysis and System Design

> **Amendment (Phase 3):** geometry is now **stored in EPSG:32737** (not 4326) and vulnerability results live in their own table. See `phase3_database_design.md`. Sections 3 to 6 below that mention EPSG:4326 storage or EPSG:32637 are superseded.

Author context: Andrew Kigondu, ICS 4A, Strathmore University. Supervisor: Dickson Owuor. Date: 13 Nov 2026.
Status: design only. No application code is included. Items marked **[CONFIRM]** need your decision before Phase 2.

---

## 0. Sources used and what was not found

**There is no `reference/` directory.** I looked in the workspace, `~/aquamap` and `~/aquamap-rebuild` (empty). I used these instead:

| Tag | Source | Role |
|---|---|---|
| **P3** | `AndrewKigondu_166228_DicksonOwuor.pdf` (43 pp, 12 Nov 2026) | Newest proposal. Treated as the **authoritative academic requirement**. Chapter 4 diagrams (use case, activity, sequence, class, ERD, wireframes) were read as images. |
| **BP** | `Infrastructure_Risk_Mapping_Capstone_Implementation_Blueprint.pdf` (30 pp) | Generic blueprint covering roads, bridges and settlements. Used as **design guidance only** where it does not add scope. |
| **LEG** | `~/aquamap` (README, `compute_risk_scores.py`, `ingest_to_postgis.py`, `preprocess_raster.py`, `api/main.py`, `tests/test_risk_engine.py`, `risk-config.js`, `requirements.txt`, `docker-compose.yml`) | Existing implementation. Treated as **evidence of what works and what to fix**, not as a requirement. |
| P1, P2 | Earlier proposals (Jun and Sep 2026) | Superseded by P3. |

I did not read `~/aquamap/.env`. No credentials are repeated here.

**Precedence rule used for conflicts:** your Phase 1 instruction > P3 > BP (only where it adds no scope) > LEG.

---

## 1. Requirements Specification

### 1.1 Purpose and scope

A web-based geospatial decision support system that shows the **relative flood vulnerability of road segments in Nairobi County**, using a static Infrastructure Vulnerability Index (IVI) built from elevation, slope, distance to rivers and road surface.

**In scope:** road segments only; precomputed IVI; interactive map; filtering; segment detail; GeoJSON export; admin pipeline and parameters; authentication.
**Out of scope (P3 roads-only decision):** bridges, settlements, inspections and photos, drone imagery, rainfall or live flood feeds, routing, notifications.

### 1.2 Actors (P3 use case diagram)

| Actor | Needs |
|---|---|
| Municipal Transport Planner | Find vulnerable links, prioritise mitigation |
| Emergency Response Coordinator | See compromised corridors and plan around them |
| System Administrator | Ingest layers, run scoring, configure parameters |

The planner and coordinator have identical permissions in every document, so they are **one data role (`user`)** and one admin role (`admin`). **[CONFIRM A8]**

### 1.3 Functional requirements

Priority: **M** = Must, **S** = Should, **C** = Could (only if time allows). "Src" shows where the requirement comes from.

| ID | Requirement | Src | Pri |
|---|---|---|---|
| FR-01 | Show all scored road segments on a Leaflet map (Canvas renderer) over an OSM base map, centred on Nairobi. | P3 UC "View Interactive Road Risk Map", BP FR1 | M |
| FR-02 | Colour each segment by risk tier (Low / Medium / High / Severe) with a legend. | P3 wireframe, BP FR5 | M |
| FR-03 | Clicking a segment shows name, road class, surface, elevation, slope, distance to nearest river, IVI, tier and per-factor scores. | P3 UC "Inspect Road Segment Details", BP FR6 | M |
| FR-04 | Filter visible roads by risk tier and by road class. | P3 UC "Filter Roads by Risk Tier", BP FR9 | M |
| FR-05 | Search roads by name (case-insensitive, partial). | LEG, BP FR9 | S |
| FR-06 | Show summary statistics: analysed road length (km) and length per tier. | P3 wireframe header cards | S |
| FR-07 | Export the currently filtered roads as GeoJSON. | P3 UC "Export GeoJSON Data" | S |
| FR-08 | Register, log in and log out. Viewing the map requires a session. | P3 UC "Authenticate User" (`<<include>>` from View Map), P3 ERD `users` | M |
| FR-09 | Email one-time-code second factor at login. | LEG, your earlier request. **Not in P3.** | C **[CONFIRM A6]** |
| FR-10 | Admin loads road, river and DEM source layers into the system (CLI first). | P3 UC "Ingest Road and Elevation Layers" | M |
| FR-11 | Admin runs the scoring pipeline and sees when it last ran and how many segments were scored. | P3 UC "Calculate Road Vulnerability Indices", wireframe | M |
| FR-12 | Admin edits scoring parameters (weights, breakpoints, tier thresholds); changes take effect on the next pipeline run. | P3 UC "Configure System Parameters" | S |
| FR-13 | Admin page lists data layers with source, type, feature count, update date and status. | P3 wireframe "Layer management" | C |
| FR-14 | "Roads near me" using browser geolocation. | LEG endpoint, earlier practicality upgrades. **Not in P3.** | C **[CONFIRM A7]** |

Explicitly **not** requirements: FR-style items from BP for inspections, photos, scenario slider, PDF/CSV reports, public settlement map, drone data.

### 1.4 Non-functional requirements

| ID | Category | Requirement | Src |
|---|---|---|---|
| NFR-01 | Performance | A viewport request returns in under 3 s at the 95th percentile on a laptop-class host. Payload per request is capped (see §6). | BP NFR, P3 3.7 |
| NFR-02 | Scalability | Handles the full county network (about 52 k segments in LEG; the P3 wireframe mock shows 18 k, see A3) without loading it all at once. | LEG |
| NFR-03 | Security | Passwords stored as salted adaptive hashes; parameterised SQL only; HTTP-only session cookie; role checks on admin routes; secrets from environment variables, never in source; HTTPS in deployment. | BP, LEG |
| NFR-04 | Usability | A first-time user can find a road and read its tier within 5 minutes; legend always visible; colours distinguishable by colour-blind users (colour plus line style). | BP, LEG `risk-config.js` |
| NFR-05 | Explainability | Every score can be decomposed into its four factor scores and weights. The UI says "relative susceptibility" and never "forecast" or "will flood". | BP, P3 limitations |
| NFR-06 | Reproducibility | Same inputs and same config produce the same scores. Each run records the config version and input layer versions. | New (derived from academic need) |
| NFR-07 | Maintainability | IVI logic lives in one module used by both the pipeline and tests. Config separate from code. | BP |
| NFR-08 | Portability | Entire stack starts with `docker compose up`; runs on a free-tier host. | P3, BP |
| NFR-09 | Data quality | Missing or invalid inputs are flagged, not silently replaced with invented values. Coverage of the `surface` tag is reported. | New (LEG defect) |
| NFR-10 | Responsiveness | Layout works at 360 px width. | Earlier practicality request **[CONFIRM A7]** |

### 1.5 Constraints

Stack fixed by you: Python, FastAPI, PostgreSQL/PostGIS, GeoPandas, Rasterio, NumPy, plain HTML/CSS/JS, Leaflet. No React, no build step. Data: SRTM 30 m, OSM roads, OSM waterways as river centrelines, Nairobi County boundary.

---

## 2. Reconciliation of Contradictions

| # | Topic | P3 says | BP says | LEG does | **Decision** |
|---|---|---|---|---|---|
| 1 | Scope | Roads only (but 1.1 still mentions GeoDjango, 1.2 still mentions bridges and settlements) | Roads, bridges, settlements | Roads | Roads only. Fix P3 1.1, 1.2 and the duplicated 3.7 paragraph separately. |
| 2 | Frontend | HTML/Leaflet | React/Vite/Tailwind | Plain JS | Plain HTML/CSS/JS (your instruction). |
| 3 | IVI formula | WLC, weights E 0.35 / River 0.30 / Slope 0.20 / Surface 0.15 | Risk = H × V × E | WLC, weights **0.35 / 0.25 / 0.25 / 0.15** | WLC with P3 weights as defaults, stored in config. **[CONFIRM A1]** |
| 4 | Score scale | 0–1 (`NUMERIC(4,3)`, sample 0.82) | 0–100 or tiers | 1.0–5.0 | Store **0–1**. UI may show ×100. Legacy 1–5 maps linearly. **[CONFIRM A2]** |
| 5 | Tier names | Low / Medium / High / **Severe** (wireframe), "Critical" in use-case wording | Low / Medium / High / **Critical** | Severe | **Severe** in data and UI. **[CONFIRM A4]** |
| 6 | CRS | All spatial columns **SRID 4326** | 4326 | Stores **32637** | Store 4326 (API-native, matches ERD). Compute metric values in 32637 inside the pipeline. See §3. **[CONFIRM A5]** |
| 7 | When scores are computed | Sequence diagram computes WLC **per request** then `UPDATE`s | Recompute endpoint | Offline script | **Batch pipeline only.** API reads stored scores. Request-time compute would make every map load slow and write to the DB on a GET. Update the sequence diagram to show the pipeline separately. |
| 8 | Header card "Active flood warnings: 12" | Shown in wireframe | n/a | n/a | **Remove.** The model is static; there is no warning feed. Replace with "Severe-tier length". Using it would contradict the "planning aid, not forecast" limitation. |
| 9 | Ingestion | Use case "Ingest layers" and a wireframe "Upload layer" button | Admin import | CLI scripts | CLI for Phase 2–3. Browser upload is a **Could** (FR-13). |
| 10 | Roles | planner, coordinator, administrator | admin, inspector, viewer | `analyst` default | `admin` + `user`. |
| 11 | River source | "Verified water resource archives" (P1/P2); wireframe "OSM waterway" | n/a | OSM waterways, 454 lines | **OSM waterways** (what actually exists). Document as a limitation. |
| 12 | Road count | Wireframe 18,342 segments | n/a | 51,947 segments | Wireframe numbers are illustrative. Real count depends on which `highway` classes are kept. **[CONFIRM A3]** |
| 13 | Material factor | "Surface susceptibility" | "Material" | OSM `surface` tag with neutral default | OSM `surface` as a proxy. P3 wireframe shows 61% tag coverage; report coverage, do not hide it. |

### 2.1 Defects found in the legacy code (to avoid repeating)

| Where | Problem | Rebuild fix |
|---|---|---|
| `api/main.py` `GET /roads` | A stray `;` inside the subquery makes the SQL invalid. Even if fixed, it returns the first 1,500 rows by `id`, an arbitrary slice of the county, not the roads in view. | Bounding-box query (§6). |
| `compute_risk_scores.py` | Weights 0.25/0.25 differ from P3. Missing DEM values replaced by invented 1650 m and 3 %. SRTM void value (-32768) is not caught by `isnan`. | Config-driven weights; NULL and flag; explicit nodata mask. |
| same | Samples only the segment **centroid**; long segments get one value. | Sample along the line (§5). |
| same | River distance via `MIN(ST_Distance)` subquery over every river per road (about 52 k × 454). | Spatial nearest join in GeoPandas, or `<->` KNN with GiST. |
| same / ingest | `if_exists="replace"` drops and rebuilds tables, losing indexes, constraints and any other columns. | Create schema once; load with truncate-and-insert inside a transaction. |
| ingest / scoring | Database credentials hard-coded in source. | Environment variables. |
| `requirements.txt` | Missing geopandas, rasterio, sqlalchemy, psycopg, PyJWT. `passlib` plus `bcrypt 5.x` is a known incompatibility. | Complete pinned requirements; use `argon2-cffi` or `bcrypt` directly. |
| README | Header and whole block duplicated; code fence broken. | Rewrite. |
| `risk_config` vs API | Tier thresholds duplicated in four SQL `CASE`s and the JS file. | One tier definition in the config table; API returns `risk_category`; JS only styles. |

---

## 3. System Architecture

### 3.1 Style

Three-tier web application plus an **offline processing pipeline** (P3 "three-tier, OOAD"). Roads are precomputed; the web tier only reads.

```
                         OFFLINE (admin, CLI)                        ONLINE (users)

 SRTM DEM ─┐                                                    ┌──────────────────────┐
 OSM roads ─┼─► pipeline/ (GeoPandas · Rasterio · NumPy) ─────► │ PostgreSQL + PostGIS │
 OSM rivers ┘   fetch → prepare → load → score (core/ivi)      │  road_segments       │
                                                               │  river_centerlines   │
                                                               │  scoring_config      │
                                                               │  users, auth_otps    │
                                                               └──────────┬───────────┘
                                                                          │ SQL (SQLAlchemy, parameterised)
                                                               ┌──────────▼───────────┐
                                  GeoJSON / JSON over REST     │ FastAPI app          │
       Browser  ◄─────────────────────────────────────────────►│ routers → services   │
  HTML + CSS + JS + Leaflet (Canvas)   (EPSG:4326, bbox)       │ serves /static too   │
                                                               └──────────────────────┘
```

### 3.2 Components and responsibilities

| Component | Responsibility | Must not |
|---|---|---|
| `core/ivi.py` | Pure functions: normalise factors, apply weights, assign tier. No I/O. | Touch the database or files. |
| `pipeline/` | Fetch and prepare data, compute metrics, call `core/ivi`, write results. | Run inside web requests. |
| `app/routers` | HTTP parsing, auth dependency, response models. | Contain SQL or scoring maths. |
| `app/services` | SQL queries, GeoJSON assembly, auth logic, email sending. | Recompute scores. |
| `static/` | Pages, Leaflet map, filters, popups, styling by tier. | Hold business rules; tier thresholds come from the API. |
| PostGIS | Storage, spatial indexes, bbox and nearest queries. | |

Maps onto the P3 class diagram: `RoadSegment`, `RiverCenterline`, `ElevationRaster` (pipeline inputs), `VulnerabilityCalculator` (= `core/ivi` + `pipeline/score`), `SpatialRepository` (= `app/services/roads_query`), `FastAPIRouteHandler` (= `app/routers`).

### 3.3 Key design decisions

1. **Static, precomputed scores.** Matches P3 and keeps every request fast.
2. **Single FastAPI process serves API and static files.** One container for the app, one for PostGIS. No separate frontend server or CORS setup.
3. **Server-side filtering plus bbox.** The browser never receives the full network.
4. **Config in the database** (`scoring_config`), versioned, so FR-12 works and NFR-06 holds.
5. **Plain JS modules, no bundler.**

### 3.4 Runtime flows

**View map:** browser requests `/` → session cookie checked → page loads → JS calls `GET /api/v1/roads?bbox=…&tier=…` on load and after each pan/zoom → PostGIS GiST bbox scan → GeoJSON → Leaflet Canvas draws styled lines.
**Score run (admin CLI):** read active config → load layers → compute metrics → score with `core/ivi` → write inside one transaction → record run.

---

## 4. Database Design

PostgreSQL 16 with PostGIS 3.4 (the image already used in LEG). All geometries **SRID 4326** per P3 ERD.

### 4.1 Tables

**`users`** (P3 ERD, plus fields LEG already needs)

| Column | Type | Notes |
|---|---|---|
| user_id | UUID PK | |
| email | VARCHAR UNIQUE NOT NULL | stored lower-case |
| password_hash | VARCHAR NOT NULL | salted adaptive hash (Argon2id or bcrypt) |
| full_name | VARCHAR | LEG |
| role | VARCHAR NOT NULL | CHECK in (`admin`,`user`) |
| is_active | BOOLEAN default true | LEG |
| created_at | TIMESTAMPTZ | |

**`auth_otps`** (only if FR-09 confirmed): otp_id, user_id FK, otp_hash, expires_at, attempts, consumed, created_at.

**`spatial_layers`** (P3 ERD: layer provenance)

| Column | Type |
|---|---|
| layer_id | INT PK |
| layer_name, layer_type, source | VARCHAR (e.g. `roads`, `rivers`, `dem`; `OpenStreetMap`, `SRTM 30m`) |
| source_url, license | VARCHAR (for the report's data chapter) |
| feature_count | INT |
| uploaded_at | TIMESTAMPTZ |

**`road_segments`** (P3 ERD)

| Column | Type | Notes |
|---|---|---|
| road_id | BIGINT PK | |
| layer_id | INT FK → spatial_layers | |
| osm_id | BIGINT | source id; may repeat if a way is split |
| name | VARCHAR | often NULL |
| highway_type | VARCHAR NOT NULL | |
| surface | VARCHAR | raw OSM tag |
| length_m | REAL | computed in EPSG:32637 |
| elevation_m | REAL | mean along the line |
| slope_pct | REAL | mean along the line |
| river_dist_m | REAL | to nearest river |
| score_elevation, score_river, score_slope, score_surface | REAL | each 0–1 (explainability, NFR-05) |
| vulnerability_index | NUMERIC(4,3) | 0–1, CHECK between 0 and 1 |
| risk_category | VARCHAR | Low / Medium / High / Severe |
| data_flags | VARCHAR | e.g. `no_dem`, `surface_missing` |
| config_version | INT FK → scoring_config | which config produced this score |
| geom | geometry(LineString, 4326) NOT NULL | |

LineString (not generic Geometry); multi-part lines are exploded at load.

**`river_centerlines`** (P3 ERD): river_id PK, name, waterway (river/stream/canal/drain), `geom geometry(LineString,4326)`.

**`scoring_config`** (supports "Configure System Parameters"; BP's optional `risk_config`)

| Column | Type | Notes |
|---|---|---|
| config_version | SERIAL PK | |
| weights | JSONB | `{elevation, river, slope, surface}`; must sum to 1 |
| breakpoints | JSONB | per factor, see §5 |
| surface_map | JSONB | OSM surface value → 0–1 |
| tier_thresholds | JSONB | e.g. 0.25 / 0.50 / 0.75 |
| is_active | BOOLEAN | exactly one active |
| created_by, created_at | | |

**`pipeline_runs`** (small, supports FR-11): run_id, config_version, started_at, finished_at, segments_scored, segments_flagged, status, input_summary JSONB.

No history table for scores; each run overwrites. **[CONFIRM A9]** (BP implies risk history only because of inspections, which are out of scope.)

### 4.2 Indexes and constraints

- GiST on `road_segments.geom` and `river_centerlines.geom` (P3 requirement).
- B-tree on `risk_category`, `vulnerability_index`, `highway_type`.
- Name search: B-tree on `lower(name)` with `text_pattern_ops` for prefix match; `pg_trgm` GIN only if substring search proves too slow. **[CONFIRM A10]**
- CHECK constraints on role, 0–1 score ranges and tier values.
- Weights sum validated in the application (and optionally a trigger).

### 4.3 Migrations

Plain numbered SQL files in `db/init/` run by the Postgres container on first start. Alembic is optional (BP uses it); skip unless the schema is expected to evolve after the demo.

---

## 5. Geospatial Processing Workflow

All steps run offline in `pipeline/`. Each is idempotent and logs counts.

| Step | Action | Tools | Checks |
|---|---|---|---|
| 1. Fetch | Download SRTM tile(s) for Nairobi; OSM roads and waterways for the county boundary; record provenance in `spatial_layers`. | requests / OSMnx or Overpass | Bounding boxes overlap Nairobi. |
| 2. Prepare DEM | Reproject DEM to EPSG:32637 (bilinear). Compute slope % from the gradient with the real pixel size. Mask SRTM void (-32768) as nodata. | Rasterio, NumPy | CRS, nodata value, min/max plausible. |
| 3. Clean vectors | Drop null/empty geometry, `make_valid`, explode multi-parts, clip to county polygon, keep only chosen `highway` classes. | GeoPandas | Counts before/after logged. |
| 4. Load | Write to PostGIS in 4326, inside a transaction (truncate then insert), build GiST indexes. | GeoPandas `to_postgis` / SQLAlchemy | Row counts match. |
| 5. Metrics | In an in-memory EPSG:32637 copy: (a) `length_m`; (b) elevation and slope sampled every ~30 m along each line, aggregated by mean; (c) distance to nearest river with a spatial nearest join. | GeoPandas, Rasterio sampling, NumPy | Nodata samples excluded; segment with none → `no_dem` flag. |
| 6. Score | Call `core/ivi` with the active config; assign tier. | `core/ivi` | All scores in [0,1]; weights sum to 1. |
| 7. Persist | Update `road_segments` factor, score and tier columns plus `config_version`; write `pipeline_runs`. | SQLAlchemy | Counts by tier logged. |
| 8. Verify | Summary statistics and a QGIS visual spot-check. | QGIS | Top-risk roads plausible. |

Rationale for working in 32637: degrees are not metres, so distances and lengths need a projected CRS. Storage stays 4326 for the API and Leaflet. **[CONFIRM A5]** (Alternative: store 32637 and transform on output, as LEG does; slower per request.)

Known limits to document: SRTM is a 30 m surface model including some buildings and vegetation; OSM waterways omit many drains and culverts; OSM `surface` is incomplete.

---

## 6. IVI Calculation Methodology

### 6.1 Model

Weighted Linear Combination (P3 3.x, from Tariq et al. 2021):

`IVI = w_elev·S_elev + w_river·S_river + w_slope·S_slope + w_surf·S_surf`, each `S` in [0,1], weights sum to 1. **Higher = more vulnerable.**

### 6.2 Default weights

| Factor | Weight (P3) | LEG code |
|---|---|---|
| Elevation | 0.35 | 0.35 |
| River proximity | 0.30 | 0.25 |
| Slope | 0.20 | 0.25 |
| Surface susceptibility | 0.15 | 0.15 |

Default = P3 (the examined document; also shown in the P3 wireframe). Weights live in `scoring_config`, so changing them is a config change, not a code change. **[CONFIRM A1]**
Caveat to state in the report: Tariq et al. weights come from AHP on a flood-susceptibility study. Applying them to road vulnerability is an adoption, not a derivation, and no sensitivity analysis exists yet. A small sensitivity run (±0.05 per weight) is a recommended Phase 6 item.

### 6.3 Factor normalisation (initial breakpoints)

LEG uses five classes per factor, scored 1–5. Rescaling with `(s−1)/4` gives 0, 0.25, 0.5, 0.75, 1. Keep these as the starting point.

| Factor | Direction | Breakpoints → score |
|---|---|---|
| Elevation (m) | lower = riskier | LEG: <1500 → 1.0, <1600 → 0.75, <1700 → 0.5, <1800 → 0.25, else 0 |
| River distance (m) | closer = riskier | <50 → 1.0, <150 → 0.75, <300 → 0.5, <500 → 0.25, else 0 |
| Slope (%) | flatter = riskier | <2 → 1.0, <5 → 0.75, <10 → 0.5, <15 → 0.25, else 0 |
| Surface | unpaved = riskier | unpaved, dirt, earth, ground, mud, gravel, sand → 1.0; paved, asphalt, concrete, paving_stones, sett → 0.25; missing or other → 0.5 |

Two issues to settle in Phase 2 before freezing these numbers:

- **Elevation classes may not discriminate.** They are absolute heights, and the first class (<1500 m) may be empty for Nairobi. The Blueprint itself warns Nairobi is fairly flat. Phase 2 should print the DEM and road-elevation distribution first. Options: keep absolute classes (simple, documented), or use percentile-based breakpoints computed from the county's own distribution. **[CONFIRM A11]**
- **Step classes are coarse.** Piecewise-linear interpolation between the same breakpoints would avoid cliff edges. This is an optional refinement; class-based is easier to explain and matches P3 wording.

### 6.4 Tiers

On the 0–1 scale, equivalent to LEG's 1–5 cut-offs: **Low < 0.25 ≤ Medium < 0.50 ≤ High < 0.75 ≤ Severe**. Stored in `scoring_config`; the API returns the tier name, so the frontend never recomputes it.

### 6.5 Missing data policy

| Case | Handling |
|---|---|
| No DEM coverage or all samples nodata | `data_flags = no_dem`; IVI left NULL; excluded from map; counted in run summary |
| No river within the area | Treat distance as the largest class (score 0) and log it |
| Surface missing | Neutral 0.5 and `surface_missing` flag (report % coverage; do not hide) |

### 6.6 Presentation rules (NFR-05)

Popup shows the four factor scores and weights. Wording: "relative susceptibility to surface flooding", plus a standing note that the index is static, ignores rainfall and drainage infrastructure, and has not been validated against flood records.

---

## 7. API Endpoint Specification

Base path `/api/v1` (P3 sequence diagram). JSON everywhere; spatial responses are GeoJSON in EPSG:4326. Interactive docs at `/docs`. Session via HTTP-only cookie. Roles: **P** public, **U** any signed-in user, **A** admin.

### 7.1 Pages (HTML)

| Route | Access | Purpose |
|---|---|---|
| `GET /` | U (else redirect to `/login`) | Map dashboard |
| `GET /login`, `/register`, `/verify-2fa` | P | Auth pages |
| `GET /admin` | A | Layers and parameters |

### 7.2 Auth

| Method and path | Access | Description |
|---|---|---|
| POST `/auth/register` | P | Name, email, password, confirmation. 409 if email exists. |
| POST `/auth/login` | P | Verifies password. Without 2FA: sets session. With 2FA (FR-09): returns pending token and emails a code. |
| POST `/auth/verify-2fa` | P (pending token) | Verifies 6-digit code (5 attempts, 5 min). Sets session. |
| POST `/auth/resend-2fa` | P (pending token) | New code. |
| POST `/auth/logout` | U | Clears cookie. |
| GET `/auth/me` | U | Current user and role. |

Forgot/reset/change password exist in LEG but are **not in P3 or BP requirements**; excluded unless confirmed. **[CONFIRM A6]**

### 7.3 Roads

| Method and path | Access | Parameters | Returns |
|---|---|---|---|
| GET `/roads` | U | `bbox=minLon,minLat,maxLon,maxLat` (required), `tier` (repeatable), `highway` (repeatable), `min_ivi`, `limit` (default 5000, max 10000) | FeatureCollection. Properties: id, name, highway_type, risk_category, vulnerability_index. Light payload for drawing. Geometry simplified at low zoom via `zoom`. |
| GET `/roads/{road_id}` | U | | One Feature with full detail: surface, elevation_m, slope_pct, river_dist_m, four factor scores, weights, flags, config_version. |
| GET `/roads/search` | U | `q` (min 2 chars), `limit` | Matching roads, one per name, with bbox to zoom to. |
| GET `/roads/summary` | U | `tier`, `highway`, optional `bbox` | Total km and km per tier (FR-06). |
| GET `/roads/export` | U | same filters as `/roads` | GeoJSON download (FR-07), full properties, size cap with 413 beyond it. |
| GET `/roads/nearby` | U | `lat`, `lon`, `radius_m` ≤ 2000 | Nearest segments (FR-14, **[CONFIRM A7]**). |
| GET `/meta/config` | U | | Active tier thresholds, labels, weights and legend styling data (single source for the UI). |

Validation: bbox must be within the Nairobi County envelope (LEG's box: lat −1.45…−1.15, lon 36.60…37.10) and within a maximum area, otherwise 422.

### 7.4 Admin

| Method and path | Access | Description |
|---|---|---|
| GET `/admin/layers` | A | Layer list with provenance and counts (FR-13). |
| GET `/admin/config` | A | Active scoring config. |
| PUT `/admin/config` | A | Create a new config version (weights must sum to 1; thresholds ascending). |
| POST `/admin/pipeline/run` | A | Start a scoring run (background task) **[CONFIRM A12]**. |
| GET `/admin/pipeline/status` | A | Last and current run. |
| GET `/health` | P | Liveness plus DB check. |

If A12 is declined, scoring stays CLI-only and the last two rows become read-only status.

### 7.5 Errors and conventions

Standard JSON `{detail: …}`, status 401 unauthenticated, 403 wrong role, 404 missing, 422 invalid input, 429 on repeated login or OTP failures. No stack traces to clients.

---

## 8. Frontend Page Structure

Plain HTML/CSS/JS, no build step; Leaflet from a pinned CDN version (or vendored).

| Page | Contents (from P3 wireframes) |
|---|---|
| `login.html` | Email, password, link to register |
| `register.html` | Name, email, password, confirm |
| `verify-2fa.html` | 6-digit code, resend (only if FR-09) |
| `index.html` (dashboard) | Top bar (brand, nav, user menu); summary cards (analysed km, Severe km, High km); left panel: study zone (Nairobi County), layer toggles (roads, rivers), tier checkboxes, road class filter, search box; map with legend; popup / detail drawer; export button |
| `admin.html` | Layer table; scoring weights and thresholds form; last run status |

**Wireframe changes:** drop "Active flood warnings"; the "Reports" tab becomes the Export action (A13); "Elevation grid" toggle is a **Could** (needs a raster overlay not otherwise planned); "Recalculate vulnerability matrix" and "Run full pipeline" buttons appear only if A12 is confirmed.

JS modules: `api.js` (fetch wrapper, 401 → login), `map.js` (Leaflet Canvas, moveend → reload by bbox), `filters.js`, `popup.js`, `risk-config.js` (styles only; tier names and thresholds from `/meta/config`), `summary.js`, `auth.js`.

Style rules: tier colours from the Okabe-Ito based palette already in LEG (Low sky blue, Medium orange, High vermilion, Severe indigo) plus line weight/dash so the map is not colour-only.

---

## 9. Testing Strategy

| Level | What | Tools |
|---|---|---|
| Unit | `core/ivi`: each normaliser at, below and above every breakpoint; weights sum; tier boundaries; hand-computed IVI for 5 profiles (max risk, min risk, mixed, missing surface, boundary values). LEG's two extreme-profile tests port across with weights updated. | pytest |
| Pipeline | Tiny fixture dataset (about 20 roads, 3 rivers, 20×20 DEM): check CRS, nodata handling, sampling along line vs centroid, nearest-river distance against hand-measured values, idempotent re-run, void-pixel case. | pytest, Rasterio in-memory |
| Database | Schema applies cleanly; CHECK constraints reject bad values; GiST index used (`EXPLAIN`) for bbox queries. | pytest + disposable PostGIS container |
| API integration | Each endpoint: valid response, valid GeoJSON, filters, bbox limits, auth (401/403), SQL injection strings, large bbox rejected. | pytest + FastAPI TestClient |
| Security | Password stored hashed (never plaintext); login and OTP rate limits; cookie flags; no secrets in repo (secret-scanning in CI). | pytest, CI |
| Frontend | Manual checklist (map renders, legend, filters, popup, export, 360 px layout) plus optional Playwright smoke test (load, filter, click). | Playwright (optional) |
| Performance | p95 of a typical viewport request under 3 s against the full dataset; payload size recorded. | script plus timings |
| Data validation | Spot-check 10 well-known roads (e.g. Mombasa Rd, Outer Ring Rd near Nairobi River) in QGIS; **historical event comparison (1–2 events) is reported as plausibility, not accuracy**. | QGIS, notebook |

Traceability: each FR/NFR ID maps to at least one test; the table is maintained in `docs/traceability.md`. CI (GitHub Actions) runs unit, pipeline and API tests on each push.

---

## 10. Recommended Project Directory Structure

```
aquamap/
├── README.md                    # one clean copy; setup and runbook
├── docker-compose.yml           # db + app
├── Dockerfile
├── .env.example                 # variable names only; real .env is git-ignored
├── .gitignore
├── requirements.txt             # runtime, pinned (incl. geopandas, rasterio, psycopg, sqlalchemy)
├── requirements-dev.txt         # pytest, httpx, etc.
├── docs/
│   ├── phase1_requirements_and_design.md
│   ├── traceability.md
│   ├── data_provenance.md
│   └── runbook.md
├── db/
│   └── init/                    # numbered SQL run on first DB start
│       ├── 001_extensions.sql
│       └── 002_schema.sql
├── data/                        # git-ignored except .gitkeep
│   ├── raw/{dem,osm,rivers}/
│   └── processed/
├── core/                        # shared, pure, no I/O
│   ├── ivi.py                   # normalisers, WLC, tiers
│   └── config_defaults.py       # default weights, breakpoints, thresholds
├── pipeline/                    # offline GeoPandas/Rasterio steps
│   ├── fetch_data.py
│   ├── prepare_dem.py
│   ├── load_vectors.py
│   ├── compute_metrics.py
│   ├── score_roads.py
│   └── run_pipeline.py          # orchestrates steps 1–8
├── app/                         # FastAPI
│   ├── main.py
│   ├── settings.py              # env-driven config
│   ├── db.py
│   ├── deps.py                  # current_user, require_admin
│   ├── routers/{pages,auth,roads,admin,meta,health}.py
│   ├── schemas/                 # Pydantic models
│   └── services/{auth,roads_query,export,email}.py
├── static/
│   ├── index.html  login.html  register.html  verify-2fa.html  admin.html
│   ├── css/style.css
│   └── js/{api,map,filters,popup,summary,auth,risk-config}.js
├── tests/
│   ├── conftest.py
│   ├── fixtures/                # tiny roads, rivers, DEM
│   ├── unit/                    # core/ivi
│   ├── pipeline/
│   ├── api/
│   └── db/
└── .github/workflows/ci.yml
```

Change from LEG: separate `core/` (pure scoring) from `pipeline/` and `app/`, instead of loose scripts and a 27 KB `main.py`. LEG's `backend/app/…` and `api/…` directories are not carried over.

---

## 11. Assumptions Requiring Your Confirmation

Recommended defaults are used if you do not object.

| ID | Question | Default used |
|---|---|---|
| A1 | IVI weights: P3 (0.35 / 0.30 / 0.20 / 0.15) or LEG (0.35 / 0.25 / 0.25 / 0.15)? | **P3** |
| A2 | Score scale: store 0–1 (P3) rather than 1–5 (LEG)? | **0–1** |
| A3 | Which OSM `highway` classes count as the "vehicular road network"? (LEG kept 51,947 segments; the P3 mock shows 18,342.) Include footways, tracks, service roads? | Motorway to residential, unclassified, service; exclude footway/path/steps/cycleway. **Please confirm.** |
| A4 | Top tier named **Severe** (P3 wireframe, LEG) rather than "Critical" (BP)? | **Severe** |
| A5 | Store geometry in 4326 and compute in 32637 in the pipeline (rather than store 32637)? | **4326 storage** |
| A6 | Authentication scope: login and registration only, or also email 2FA, password reset and change? P3 lists only "Authenticate User". | Register, login, logout in baseline; **2FA built last as an optional module**; reset/change excluded |
| A7 | Are mobile-first layout, geolocation ("roads near me") and PWA still wanted? None is in P3. | Responsive layout only (NFR-10); geolocation Could; PWA, saved places, share links excluded |
| A8 | Roles: `admin` and `user` (planner = coordinator)? | **Yes** |
| A9 | Keep only the latest score per road (no history)? | **Yes** |
| A10 | Name search: prefix match first, trigram later if needed? | **Yes** |
| A11 | Elevation scoring: absolute LEG classes or percentile-based breakpoints from the DEM? Decide after Phase 2 prints the distribution. | Absolute for now |
| A12 | Should admin be able to trigger the pipeline from the browser, or CLI only? | **CLI only for MVP** |
| A13 | Is "Reports" in the P3 wireframe just the GeoJSON export? | **Yes** |
| A14 | River data: OSM waterways acceptable as "river centrelines" despite P1/P2 wording about water-resource archives? | **Yes**, stated as a limitation |
| A15 | Leaflet and fonts: CDN or vendored files for the offline demo? | **Vendored** (safer for a demo without internet) |
| A16 | Hosting: local Docker for the defence, free-tier host later (domain not yet specified)? | **Yes** |

### Proposal text to fix separately (not part of the build)

P3 1.1 still names GeoDjango; 1.2 still mentions bridges and settlements; 3.7 duplicates the FastAPI paragraph; the sequence diagram should show scoring as a batch step; the wireframe card "Active flood warnings" should go; list of figures/tables is incomplete.

---

## 12. Suggested next phases (for planning only)

1. **Phase 2:** schema, config, data fetch and DEM/vector preparation, with distribution summaries to settle A3 and A11.
2. **Phase 3:** `core/ivi` plus tests, then pipeline scoring.
3. **Phase 4:** API.
4. **Phase 5:** frontend.
5. **Phase 6:** auth hardening (and 2FA if confirmed), validation, sensitivity check, packaging.
