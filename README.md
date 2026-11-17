# AquaMap

A web-based geospatial decision support system showing the **relative flood vulnerability of road
segments in Nairobi County**. Each road gets an Infrastructure Vulnerability Index (IVI) from
elevation, slope, distance to rivers and road surface. The index is a static planning aid, not a flood
forecast.

**Stack:** Python 3.12, FastAPI, PostgreSQL/PostGIS, GeoPandas, Rasterio, NumPy, plain HTML/CSS/JS, Leaflet.

> **Status:** data pipeline (Phases 4–7), road queries and REST API (8–9), accounts (10), map dashboard (11),
> automated tests (12), Docker and CI (13), end-to-end verification (14). **Final report:**
> [`docs/phase14_final_report.md`](docs/phase14_final_report.md). Not built yet: GeoJSON export, a second factor at sign-in, the admin
> page and saving scoring configurations from the browser (those endpoints answer `501 Not Implemented`).
> Design: [`docs/phase1_requirements_and_design.md`](docs/phase1_requirements_and_design.md) and
> [`docs/phase3_database_design.md`](docs/phase3_database_design.md) (the latter amends the former).

## Quick start (Docker)

Needs Git, Docker with Compose v2, Python 3 (only to create `.env`) and internet access for the first data
download. From a clean clone:

```bash
git clone git@github.com:Kigondinho/aquamap-rebuild.git
cd aquamap-rebuild

python3 scripts/init_env.py                    # creates .env with random secrets (git-ignored, readable by you only)
docker compose up -d --build --wait            # PostGIS + app; the app creates the database schema on start
docker compose --profile pipeline run --rm pipeline    # download, process and score the data (first time ~10 min)
docker compose exec app python -m app.db_tools create-user --email you@example.com --name "Your Name" --admin
```

Open <http://127.0.0.1:8000>, sign in, and the dashboard shows the scored roads. API documentation:
<http://127.0.0.1:8000/docs>. If port 8000 or 5433 is taken: `python3 scripts/init_env.py --force --app-port 8080
--db-port 5434` before the first start.

| Task | Command |
|---|---|
| Status of the containers | `docker compose ps` |
| Logs of the app | `docker compose logs -f app` |
| Stop / start again (data is kept) | `docker compose down` / `docker compose up -d --wait` |
| After pulling new code | `docker compose up -d --build --wait` (the schema is upgraded on start) |
| Refresh the data | `docker compose --profile pipeline run --rm pipeline` |
| One pipeline step | `docker compose --profile pipeline run --rm pipeline python -m pipeline.run_pipeline --only score` |
| **Delete everything, including the database** | `docker compose down -v` |

How the containers, configuration and CI fit together: [`docs/phase13_deployment.md`](docs/phase13_deployment.md).

## Local development (app on your machine, database in Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate                      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt -r requirements-pipeline.txt

python3 scripts/init_env.py                    # skip if you already have .env
docker compose up -d --wait db                 # only the database
python -m app.db_tools init                    # create or upgrade the schema (safe to repeat)
python -m pipeline.run_pipeline                # data: download, process, score
python -m app.db_tools create-user --email you@example.com --name "Your Name" --admin
uvicorn app.main:app --reload                  # http://127.0.0.1:8000
```

`GET /api/v1/health` reports `"status": "ok"` when the database is reachable and `"degraded"` when it is not; the
app starts either way.

### Tests

```bash
pytest                           # quick: everything that needs no database (database tests are reported as unverified)
pytest --test-db                 # complete: also the database, pipeline and browser tests, in a SEPARATE database
pytest --test-db --report=docs/test-report.md  # the same, and save the passed / failed / unverified report
```

`--test-db` uses the database `<POSTGRES_DB>_test` (for example `aquamap_db_test`) on the server in `.env` and
creates it if needed. Tests only ever run against a database whose name ends in `_test`; anything else stops the
run before a single statement, so the development and production databases cannot be touched. The browser tests
need Firefox and geckodriver (`pip install -r requirements-dev.txt` installs Selenium); without them they are
reported as unverified. Details: [`docs/phase12_testing.md`](docs/phase12_testing.md).

To check a running installation end to end (database, API and browser must agree), see `scripts/verify_system.py`.

Write the report option as one word, `--report=FILE`. **CI** (GitHub Actions, `.github/workflows/ci.yml`) runs
the complete suite with `--require-all`, so a skipped test fails the run, and separately builds the images and
starts the whole stack from a clean checkout.

### Database

PostgreSQL 16 with PostGIS 3.4 runs in Docker. Credentials come only from `.env`; Compose will not
start without `POSTGRES_PASSWORD`. The database port is published on 127.0.0.1 only.

| Task | Command |
|---|---|
| Start | `docker compose up -d --wait db` |
| Create, or re-apply, the schema without losing data (idempotent; the app container does this on every start) | `python -m app.db_tools init` |
| Check PostGIS, tables, SRIDs and indexes | `python -m app.db_tools check` |
| Open a SQL shell | `docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'` |
| Stop | `docker compose stop db` |
| **Wipe everything and start clean** (deletes all data) | `docker compose down -v && docker compose up -d --wait` |

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

### REST API (Phase 9)

```bash
uvicorn app.main:app --reload                                   # http://localhost:8000/docs for the interactive API page
python -m app.db_tools create-user --email you@example.com --name "Your Name" --admin   # the only way to make an administrator
python -m app.openapi_export                                    # regenerate docs/openapi.json after changing the API
```

Register and sign in through `/api/v1/auth/register` and `/api/v1/auth/login`; the session is an HTTP-only cookie.
Road, statistics and metadata routes need a session; `/admin` routes need an administrator. Lists are paginated
(`limit`, `offset`, `pagination.total`), geometry is GeoJSON in EPSG:4326, and every error is `{"detail": "..."}`.
Endpoint reference: [`docs/phase9_api.md`](docs/phase9_api.md); machine-readable: [`docs/openapi.json`](docs/openapi.json).

### Accounts (Phase 10)

Registration, email verification by one-time code, sign-in, sign-out, password change and password reset. Passwords are
stored as Argon2id hashes and codes as keyed hashes; nothing is stored in clear. Sessions are HTTP-only cookies that
sign-out and password changes end on the server.

Without a mail server, development writes each email (with its code) to the server log: look for
`DEVELOPMENT EMAIL (not sent)` in the `uvicorn` output. For real delivery set `SMTP_HOST`, `SMTP_FROM` and, if needed,
`SMTP_USER` and `SMTP_PASSWORD` in `.env`. In production without SMTP, email is off: accounts need no verification and
reset by email is unavailable. Details: [`docs/phase10_authentication.md`](docs/phase10_authentication.md).

### Dashboard (Phase 11)

Open <http://localhost:8000>, create an account and sign in. The dashboard shows the road map coloured by
vulnerability category with a legend, road search, filters for category, IVI range and road class, a road information
panel, nearby roads and summary statistics. Everything shown comes from the API; the page holds no data of its own.
Details: [`docs/phase11_frontend.md`](docs/phase11_frontend.md).

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
| `SESSION_COOKIE_NAME`, `SESSION_TTL_HOURS` | Session cookie (always HttpOnly; Secure in production) | `aquamap_session`, `12` |
| `EMAIL_BACKEND` | `auto`, `smtp`, `console` (development only) or `none` | `auto` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS` | Mail server for verification and reset codes | unset, `587`, `true` |
| `OTP_TTL_MINUTES`, `OTP_MAX_ATTEMPTS`, `OTP_RESEND_SECONDS` | One-time code lifetime, wrong guesses allowed, wait between codes | `10`, `5`, `60` |
| `ENABLE_2FA` | Second factor at sign-in (not built: leave off) | `false` |
| `DATA_DIR` | Raw and processed geodata | `data` |
| `APP_PORT` | Port of the app on this machine (Docker; 127.0.0.1 only) | `8000` |
| `HOST_UID`, `HOST_GID` | User the pipeline container writes `data/` as (set by `scripts/init_env.py`) | `1000` |
| `DB_AUTO_INIT` | Containers create or upgrade the schema on start | `true` |

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
tests/        pytest suites: unit, api, pipeline, db, e2e (browser), structure
docker/       Container start-up script (schema first, then the app)
scripts/      init_env.py: creates .env with random secrets
.github/      CI workflow
data/         Raw and processed geodata (git-ignored; folders kept)
docs/         Design and phase documents
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

Done: scoring engine and data pipeline (Phases 4–7), road queries and API (8–9), accounts (10), dashboard (11),
tests (12), Docker and CI (13). Still to do: GeoJSON export, the admin page and saving configurations from the
browser, an optional second factor at sign-in, and a public deployment behind HTTPS.
