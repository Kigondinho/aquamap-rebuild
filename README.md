# AquaMap

A web-based geospatial decision support system showing the **relative flood vulnerability of road
segments in Nairobi County**. Each road gets an Infrastructure Vulnerability Index (IVI) from
elevation, slope, distance to rivers and road surface. The index is a static planning aid, not a flood
forecast.

**Stack:** Python 3.12, FastAPI, PostgreSQL/PostGIS, GeoPandas, Rasterio, NumPy, plain HTML/CSS/JS, Leaflet.

> **Status: scaffold, database layer (Phase 3), data acquisition (Phase 4), DEM and slope (Phase 5) and database
> ingestion of roads and rivers (Phase 6) the IVI scoring engine (Phase 7) and classification with road queries (Phase 8).** Authentication, export, the
> admin endpoints and the map interface are not implemented yet; those endpoints answer `501 Not Implemented`.
> Design: [`docs/phase1_requirements_and_design.md`](docs/phase1_requirements_and_design.md) and
> [`docs/phase3_database_design.md`](docs/phase3_database_design.md) (the latter amends the former).

## Prerequisites

- Python 3.12 (3.13 also works for the web app)
- Docker with Docker Compose, for the PostGIS database
- Git

## Setup

```bash
# 1. Create a virtual environment and install the web-app and test dependencies
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# 2. Create your local configuration
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste the output as SECRET_KEY in .env
# also set POSTGRES_PASSWORD in .env

# 3. Start the database (first start runs db/init/*.sql and creates the schema)
docker compose up -d db

# 4. Confirm the database is ready: PostGIS, tables, EPSG:32737 geometry columns, GiST indexes
python -m app.db_tools check

# 5. Start the API with auto-reload
uvicorn app.main:app --reload
```

Open <http://localhost:8000> for the dashboard shell and <http://localhost:8000/docs> for the
interactive API documentation. `GET /api/v1/health` reports `"status": "ok"` when the database is
reachable and `"degraded"` when it is not; the app still starts either way.

### Run everything in Docker instead

```bash
docker compose up --build        # database + app on http://localhost:8000
```

### Tests

```bash
pytest                           # unit, API and structure tests; database tests are skipped

# Also run the database tests against a real PostGIS (each test is rolled back). Use the
# password from your .env; do not save it into files you keep.
export AQUAMAP_TEST_DATABASE_URL="postgresql+psycopg://aquamap_user:<password>@localhost:5433/aquamap_db"
pytest -m db
```

### Database

PostgreSQL 16 with PostGIS 3.4 runs in Docker. Credentials come only from `.env`; Compose will not
start without `POSTGRES_PASSWORD`.

| Task | Command |
|---|---|
| Start | `docker compose up -d db` |
| Apply or re-apply the schema without losing data (idempotent) | `python -m app.db_tools init` |
| Check PostGIS, tables, SRIDs and indexes | `python -m app.db_tools check` |
| Open a SQL shell | `docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'` |
| Stop | `docker compose stop db` |
| **Wipe everything and start clean** (deletes all data) | `docker compose down -v && docker compose up -d db` |

Tables: `users`, `auth_otps`, `spatial_layers`, `scoring_config`, `pipeline_runs`, `road_segments`,
`river_centerlines`, `road_vulnerability` (results, stored separately from roads) and the view
`v_road_risk`. Geometry columns are `LineString` in **EPSG:32737** with GiST indexes. Full detail is in
`docs/phase3_database_design.md`.

If you started a database from the earlier Phase 2 scripts (which used EPSG:4326), wipe it as shown above.

### Geospatial data acquisition (Phase 4)

The pipeline needs extra libraries and is not required to run the web app. This downloads the Nairobi
County boundary, OSM roads and waterways and an SRTM 30 m DEM into `data/raw/`, writes cleaned EPSG:32737
copies to `data/processed/`, records provenance and validates everything:

```bash
pip install -r requirements.txt -r requirements-pipeline.txt
python -m pipeline.acquire all           # exit status 1 and a failure list if anything is wrong
python -m pipeline.acquire validate      # re-check what is on disk
```

Details, licences, limitations and the validation rules are in
[`docs/phase4_data_acquisition.md`](docs/phase4_data_acquisition.md). 

### DEM and slope (Phase 5)

```bash
python -m pipeline.prepare_dem           # raw DEM -> data/processed/dem_32737.tif + slope_pct_32737.tif (percent)
```

Method, validation and limitations: [`docs/phase5_dem_terrain.md`](docs/phase5_dem_terrain.md).

### Load roads and rivers into PostGIS (Phase 6)

```bash
python -m app.db_tools init              # once, if the database was created before Phase 6
python -m pipeline.load_vectors          # data/processed/*.gpkg -> road_segments, river_centerlines; then verifies
python -m pipeline.load_vectors --verify-only
```

Safe to re-run: rows are matched on `(osm_element, osm_id, part_no)`, so a second run inserts nothing. Details:
[`docs/phase6_ingestion.md`](docs/phase6_ingestion.md).

If port 5433 is already in use on your machine, set another `POSTGRES_PORT` in `.env`.

### Score the roads (Phase 7)

```bash
python -m app.db_tools init              # once, if the database was created before Phase 7
python -m pipeline.score_roads           # measure, score and store every eligible road; verifies what it stored
python -m pipeline.score_roads --verify-only
```

`IVI = 0.35·E + 0.20·S + 0.30·D + 0.15·M`, each factor scored 1–5 (5 = most vulnerable). The scoring rules are in
`core/ivi.py` (pure, unit-tested). Thresholds, missing-data rules, metadata and limitations:
[`docs/phase7_ivi_engine.md`](docs/phase7_ivi_engine.md). The index is a relative ranking, **not a flood probability**.
The combined entry point `python -m pipeline.run_pipeline` is still a stub; run the steps one by one.

### Categories and road queries (Phase 8)

Low < 2.0 ≤ Medium < 3.0 ≤ High < 4.0 ≤ Severe; a value on a threshold belongs to the higher category. The same rule
exists in Python (`core.ivi.assign_tier`) and in SQL (`ivi_tier`), and a trigger keeps stored categories consistent.
Road queries (map area, category, IVI range, road class, name search, radius, detail) are in
`app/services/roads_query.py`. Details: [`docs/phase8_classification_and_queries.md`](docs/phase8_classification_and_queries.md).

## Configuration

All settings come from environment variables or `.env` (see `.env.example`). The app refuses to start
without `SECRET_KEY` (32+ characters) and `POSTGRES_PASSWORD`. In production (`APP_ENV=production`)
placeholder values are rejected and the interactive docs are disabled.

| Variable | Purpose | Default |
|---|---|---|
| `APP_ENV` | `development`, `test` or `production` | `development` |
| `SECRET_KEY` | Signs session tokens | required |
| `POSTGRES_HOST` / `PORT` / `DB` / `USER` / `PASSWORD` | Database connection | `localhost` / `5433` / `aquamap_db` / `aquamap_user` / required |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, `DB_POOL_RECYCLE`, `DB_CONNECT_TIMEOUT`, `DB_ECHO` | Connection pool and timeouts | `5`, `10`, `30`, `1800`, `3`, `false` |
| `SESSION_COOKIE_NAME`, `SESSION_TTL_HOURS` | Session cookie | `aquamap_session`, `12` |
| `ENABLE_2FA`, `OTP_*`, `SMTP_*` | Optional email second factor | off |
| `DATA_DIR` | Raw and processed geodata | `data` |

## Project layout

```
app/          FastAPI application
  routers/      HTTP layer: auth, roads, admin, meta, health, pages
  schemas/      Pydantic request/response models
  services/     SQL, auth, tokens, email, export (business logic goes here)
  settings.py   Environment-driven configuration
  db.py         Lazy SQLAlchemy engine, pooled sessions, transaction scope
  db_tools.py   Database init and diagnostics CLI (python -m app.db_tools)
  deps.py       Auth dependencies (current user, admin only)
  errors.py     Exceptions and JSON error handling
core/         Pure scoring logic and defaults shared by app and pipeline (no I/O)
pipeline/     Offline GeoPandas/Rasterio processing, run from the command line
db/init/      Idempotent SQL: 001 extensions, 002 tables, 003 indexes, 004 views, 005 classification
static/       HTML, CSS, JS and vendored Leaflet (no build step)
tests/        pytest suites: unit, api, pipeline, db, structure
data/         Raw and processed geodata (git-ignored; folders kept)
docs/         Design documents
```

## Conventions

- Geometry is stored in EPSG:32737 (WGS 84 / UTM 37S, metres) and served to the browser as EPSG:4326.
- Risk tiers: Low, Medium, High, Severe. IVI and its four factor scores are on a 1–5 scale (5 = most vulnerable).
- The IVI is a relative vulnerability index, not a flood probability or a measured flood risk.
- SQL uses bound parameters only. Secrets never appear in source or the repository.
- Scoring rules live in `core/` only; the frontend never recomputes tiers.

## Data sources and licensing

SRTM 30 m elevation (public domain; "SRTM terrain data courtesy of the U.S. Geological Survey"),
OpenStreetMap roads and waterways (© OpenStreetMap contributors, ODbL). Each download has a
`*.provenance.json` record beside it (source, retrieval time, licence, checksums); a later phase loads
these into the `spatial_layers` table. Leaflet 1.9.4 is vendored under its BSD-2-Clause
licence (`static/vendor/leaflet/LICENSE`).

## Roadmap

1. Scoring engine (`core/ivi.py`) with unit tests: done (Phase 7).
2. Data pipeline: fetch and validate (Phase 4), DEM/slope (Phase 5), vector load (Phase 6), metrics and scoring (Phase 7) done.
3. Roads API and GeoJSON export.
4. Authentication (registration, login, optional email 2FA).
5. Map data loading, filters, popups and summary cards.
6. Validation, packaging and deployment.
