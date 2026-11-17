# AquaMap — final report

AquaMap is a web-based decision support system that ranks the road segments of Nairobi County by their
**relative vulnerability to surface flooding**. Each road gets an Infrastructure Vulnerability Index (IVI, 1–5)
from elevation, slope, distance to the nearest waterway and road surface, shown on an interactive map with
search, filters and per-road explanations.

> The IVI is a relative screening index. It is **not** a flood probability, a forecast or a measured flood risk:
> it uses no rainfall, drainage or flood-record data and has not been validated against observed flooding.

This report describes the system as built and records how each part was verified. "Verified" below always
means checked by running it; anything not run is listed as such.

## Summary of the end-to-end verification (17 November 2026)

The whole workflow was run from a **fresh clone of the GitHub repository** (commit `855a933`), with a new database
and **real downloads**, in its own Docker Compose project. Every stage was then checked against the database
directly, and the dashboard was driven in a real browser and compared with the database value by value.

| # | Workflow step | Result | Evidence |
|---|---|---|---|
| 1 | Acquire roads, rivers and DEM | **Verified.** Downloaded from Nominatim, Overpass and the SRTM bucket in 407 s: 52,469 roads, 720 waterways, DEM 1,432–2,324 m with no voids; all six validations passed | pipeline log; `data/processed/validation_report.json` |
| 2 | Preprocess elevation and slope | **Verified.** EPSG:32737, 30 m, validation passed | terrain report |
| 3 | Ingest into PostGIS | **Verified.** 52,469 roads and 720 rivers loaded and checked feature by feature; 0 invalid geometries | ingestion report; checker |
| 4 | Calculate IVI scores | **Verified.** 47,619 roads scored; re-scoring 2,000 random roads in Python from the stored measurements reproduced every stored result; river distances equal PostGIS nearest-river distances (worst 0.0001 m) | `phase14_verification.md` |
| 5 | Store results | **Verified.** Every stored IVI equals the weighted sum of its factor scores and every category matches its IVI (SQL check over all rows); the run is recorded as succeeded | same |
| 6 | Retrieve through FastAPI | **Verified.** Statistics and 40 random road details equal the database | same |
| 7 | Display on the Leaflet map | **Verified after a fix.** For five views, from the whole county to the city centre, the set of roads drawn and the category of each equal the database exactly (45,272 roads in the county view); colours follow the categories; roads are painted on the canvas | same |
| 8 | Search and filter | **Verified.** Four searches list exactly the database's names; a combined filter (High + Severe, residential, IVI ≥ 3.2) draws exactly the matching roads | same |
| 9 | Inspect road segments | **Verified.** 40 roads across all four categories opened by real mouse clicks: name, class, surface, IVI, category, elevation, slope and river distance in the panel equal the database; roads without a result say so | same |
| 10 | Authenticate and protect | **Verified.** Anonymous requests to all nine protected endpoints get 401; a normal user gets 403 on admin routes; registration → emailed code → confirmation → sign-in → password change (old session ends) → sign-out (copied cookie refused) → reset by code → lock-out after five wrong passwords, all on the fresh stack | §6.5 |

The scores the dashboard shows match the database for every road and view that was checked.

### Defects found and fixed in this phase

1. **The city-wide map was misleading.** At the dashboard's start view only the 15,000 highest-scoring of about
   45,000 roads in view were drawn: the map showed High 12,891, Medium 1,492, Low 0, while the database holds Medium
   27,419 and Low 4,345 in that view. The map now draws every scored road in view (the remaining pages are fetched
   four at a time and drawn in score order, so higher scores stay on top). The whole county takes about 3.5 s, down
   from 7.8 s for a sequential load. A new browser test with 16,000 roads fails on the old code and passes now.
2. **Every page load asked for `/favicon.ico` and got a 404.** Pages now declare an icon, and `/favicon.ico` is served.

Earlier phases found and fixed (recorded in their documents): roads unclickable after a selection (Phase 12), the
pipeline image missing `libexpat` (13), the OpenAPI check needing local secrets in CI (13), and others.

## 1. Implemented functionality

| Area | What exists | How it was verified |
|---|---|---|
| Data acquisition | Download of the county boundary (Nominatim), OSM roads and waterways (Overpass), SRTM 30 m tiles (AWS); provenance with checksums; validation | §6.4 (fresh downloads), `tests/pipeline/test_acquisition.py` |
| Terrain | Reprojection to EPSG:32737 (bilinear, nodata-aware), Horn slope in percent | §6.4, `tests/pipeline/test_prepare_dem.py`, comparison with `gdaldem` (Phase 5) |
| Ingestion | Validation, repair, de-duplication, idempotent upsert into PostGIS, verification against the files | §6.4, `tests/db/test_ingestion.py` |
| Scoring | Terrain sampled every 30 m along each road, river distance, 1–5 factor scores, weighted IVI, categories, missing-data flags, run metadata | §6.3–6.4, `tests/unit/test_ivi.py`, `tests/db/test_pipeline_end_to_end.py` |
| Classification | Low / Medium / High / Severe, one rule in Python and SQL, enforced by a trigger | §6.3, `tests/db/test_classification.py` |
| REST API | Roads by area, filters, search, nearby, detail, statistics, analysis metadata; pagination; GeoJSON in EPSG:4326 | §6.3, `tests/db/test_api_endpoints.py` |
| Accounts | Registration, email verification by code, sign-in, sign-out, password change and reset, revocable sessions, roles | §6.5, `tests/db/test_auth_flows.py` |
| Dashboard | Map coloured by category, legend, statistics, search, category / IVI / class filters, road panel, nearby roads, small-screen layout | §6.3, `tests/e2e/test_dashboard.py`, `tests/e2e/test_large_view.py` |
| Operations | Docker Compose (database, app, pipeline), automatic schema set-up, `.env` generator, CI on GitHub | §6.2, §6.6 |

Not built (they answer `501 Not Implemented` or have no page): GeoJSON export, saving a scoring configuration
from the browser, the admin page, and a second factor at sign-in.

## 2. Architecture

```
 Browser (HTML, CSS, JavaScript modules, Leaflet)
     │  HTTPS in a deployment; session in an HTTP-only cookie
     ▼
 FastAPI application (app/)            ── core/ (pure IVI rules, shared)
   routers → services → SQL (bound parameters only)
     │
     ▼
 PostgreSQL 16 + PostGIS 3.4 (EPSG:32737)
     ▲
     │  loads and scores
 Data pipeline (pipeline/, run from the command line or the pipeline container)
   acquire → terrain → load → score
```

- **`core/`** holds the scoring rules and defaults with no I/O, so the pipeline, the API and the tests use the same
  code. **`app/`** is the web layer: routers parse requests, services hold the SQL and the account logic.
  **`pipeline/`** does all heavy geospatial work offline; the web app never processes rasters.
- **Storage CRS** is EPSG:32737 (UTM 37S, metres) so lengths and distances need no conversion; the API serves
  EPSG:4326 for Leaflet.
- **Containers:** `db`, `app`, and an on-demand `pipeline` (Docker Compose). The app creates or upgrades the schema
  on every start. Ports listen on 127.0.0.1 only.
- **Frontend:** no build step, no framework. It holds no data of its own: categories, ranges, road classes, roads
  and statistics all come from the API.

## 3. Database structure

| Table | Holds | Notable rules |
|---|---|---|
| `road_segments` | OSM roads: name, class, surface, `LineString` geometry, generated length | valid, non-empty geometry; unique `(osm_element, osm_id, part_no)` |
| `river_centerlines` | OSM waterways (river, stream, canal, drain) | same geometry rules and key |
| `road_vulnerability` | One result per road: three measurements, four factor scores, IVI, category, flags, model and configuration version, run, analysis date, sample count | IVI 1–5; scores 1–5; IVI and category both set or both empty; a trigger rejects a category that contradicts its IVI |
| `scoring_config` | Versioned weights, class boundaries, surface scores, category thresholds | at most one active |
| `pipeline_runs` | Each scoring run: start, end, status, counts, inputs | status in running / succeeded / failed |
| `spatial_layers` | Loaded layers with source and licence | unique name and type |
| `users`, `auth_otps` | Accounts (Argon2id hashes) and one-time codes (keyed hashes) | lower-case unique email; codes per purpose |
| `v_road_risk` (view) | Roads joined with their result | unscored roads have empty result columns |

Indexes: GiST on both geometry columns; B-tree on road class, OSM id, IVI, category and foreign keys; trigram GIN on
lower-case road names; unique source keys used by the idempotent loader. The SQL function `ivi_tier()` mirrors the
Python classification.

## 4. Geospatial methodology

1. **Acquisition.** Nairobi County boundary from OpenStreetMap; all `highway` lines and `waterway` lines (river,
   stream, canal, drain) inside it via the Overpass API; SRTM 1-arc-second tiles S02E036 and S02E037, merged and
   clipped. Every file gets a provenance record (source, time, licence, SHA-256).
2. **Terrain.** The DEM is reprojected to EPSG:32737 on a 30 m grid snapped to whole multiples of 30 m, with bilinear
   resampling that leaves voids out. Slope is Horn's 3×3 method in percent (100·tan θ). A cell gets a slope only if
   its whole 3×3 window has data, so voids and edges never create false cliffs.
3. **Ingestion.** Geometries are validated and repaired where possible, split into LineStrings, de-duplicated on the
   OSM identity, reprojected to EPSG:32737 and loaded in one transaction; the load is verified feature by feature.
4. **Measurement.** Each eligible road (motorway to residential, unclassified, service and links) is sampled at points
   at most 30 m apart, ends included. Elevation and slope are the means of the valid samples. River distance is the
   shortest planar distance from the whole line to the nearest waterway.
5. **Scoring.** `IVI = 0.35·E + 0.20·S + 0.30·D + 0.15·M`, each factor scored 1–5:

   | Score | Elevation (m) | Slope (%) | River distance (m) | Surface |
   |---|---|---|---|---|
   | 5 | < 1500 | < 2 | < 50 | unpaved, dirt, earth, ground, gravel, … |
   | 4 | 1500–1600 | 2–5 | 50–150 | |
   | 3 | 1600–1700 | 5–10 | 150–300 | missing or unrecognised (flagged) |
   | 2 | 1700–1800 | 10–15 | 300–500 | paved, asphalt, concrete, … |
   | 1 | ≥ 1800 | ≥ 15 | ≥ 500 | |

   A value on a boundary belongs to the class above. Missing terrain gives no IVI and a flag; a missing surface gives
   the midpoint and a flag.
6. **Classification.** Low < 2.0 ≤ Medium < 3.0 ≤ High < 4.0 ≤ Severe, read from the IVI stored to three decimals.

Weights and thresholds come from the approved methodology baseline and are stored, versioned, in the database.
They are adopted, not calibrated.

## 5. API endpoints

Base path `/api/v1`. Access: **P** public, **U** signed-in user, **A** administrator. Full description:
[`openapi.json`](openapi.json) and `/docs` on a running server.

| Method and path | Access | Purpose |
|---|---|---|
| GET `/health` | P | Liveness and database check |
| POST `/auth/register` | P | Create an account (emails a code when email is configured) |
| POST `/auth/verify-email`, `/auth/resend-verification` | P | Confirm the address |
| POST `/auth/login`, `/auth/logout` | P | Sign in (sets the session cookie), sign out (ends sessions) |
| GET `/auth/me` | U | The signed-in user |
| POST `/auth/password/change` | U | Change password |
| POST `/auth/password/forgot`, `/auth/password/reset` | P | Reset by emailed code |
| GET `/roads` | U | Roads in a map area; filters: category, class, IVI range; paginated |
| GET `/roads/search` | U | Search by name; paginated |
| GET `/roads/nearby` | U | Roads within a radius, nearest first; paginated |
| GET `/roads/{road_id}` | U | One road with all stored results |
| GET `/roads/summary` | U | Statistics per category |
| GET `/analysis/metadata` | U | Model, configuration, run, coverage, sources |
| GET `/meta/config` | U | Category ranges and weights for the legend |
| GET `/admin/layers`, `/admin/config`, `/admin/pipeline/status` | A | Administrator reads |
| GET `/roads/export`, PUT `/admin/config`, POST `/auth/verify-2fa`, `/auth/resend-2fa` | — | Not built (501) |

Errors always have the body `{"detail": "..."}`; validation errors add a field list.

## 6. Test results

### 6.1 Automated test suite

`pytest --test-db --require-all` on 17 November 2026 ([`phase14_test_report.md`](phase14_test_report.md)):
**994 passed, 0 failed, 0 unverified** (nothing skipped; `--require-all` turns any skip into a failure).

| Area | Passed |
|---|---:|
| Unit: IVI, normalisation, weights, classification, missing data | 161 |
| Unit: geospatial calculations | 49 |
| Unit: data acquisition | 37 |
| Authentication | 103 |
| Unit: settings, database tooling, test-database guard | 39 |
| Integration: database, schema, constraints | 72 |
| Integration: ingestion and full pipeline | 23 |
| Integration: spatial queries, filtering, road detail | 125 |
| Integration: API responses | 148 |
| Frontend: in a real browser (headless Firefox) | 33 |
| Frontend: files and API wiring | 56 |
| Project: structure, secrets, deployment configuration | 148 |

Tests run against a separate database whose name must end in `_test`; the development database was checked
untouched afterwards.

### 6.2 Continuous integration

GitHub Actions ran both jobs successfully on commit `855a933` (the code that was cloned for this verification) and
again on `178b840` (with the Phase 14 fixes): the complete suite with `--require-all`, and the Docker start-up from a
clean checkout.

### 6.3 End-to-end checker

`scripts/verify_system.py` compares a running system's database, API and browser view. On the fresh stack, after the
fixes: **34 checks passed, 0 failed** ([`phase14_verification.md`](phase14_verification.md)).

### 6.4 Pipeline on real data

| Stage | Result |
|---|---|
| Acquire (real downloads) | 407 s; boundary, 52,469 roads, 720 waterways, SRTM tiles; six validations passed |
| Terrain | 1 s; validation passed |
| Load | 5 s; 52,469 roads and 720 rivers |
| Score | 7 s; 47,619 roads: Low 4,345 (669 km), Medium 27,641 (3,715 km), High 14,940 (2,708 km), Severe 693 (176 km) |

`surface` is missing on 89.3 % of roads (OpenStreetMap tagging); those roads use the neutral surface score and are flagged.

### 6.5 Accounts on the running system

All 16 steps passed on the fresh stack: registration with a code that is not in the response; sign-in refused until
the address is confirmed; wrong code refused; right code accepted; sign-in; data readable; password change ends the
earlier session but keeps this one; sign-out; a copied cookie refused afterwards; "forgot password" answers the
same for known and unknown addresses; reset with the emailed code; old password refused, new one accepted; five
wrong passwords lock sign-in (429). The database holds only Argon2id password hashes and keyed code hashes.

### 6.6 Operations

From the clone: `python3 scripts/init_env.py` and `docker compose up -d --build --wait` gave a healthy app and
database in 52 s, with the schema created automatically. No errors, tracebacks or 5xx responses appeared in the app
or database logs during the whole verification.

## 7. Known limitations

**Of the method (they limit what the index means):**

- The IVI has **not been validated against flood observations**. Weights and class boundaries are the documented
  baseline, adopted rather than calibrated, and no sensitivity analysis has been done.
- SRTM is a year-2000 surface model (buildings and trees included) with metres of vertical error at 30 m. It does not
  resolve embankments, bridges, culverts or local dips.
- OpenStreetMap is incomplete: `surface` is missing on 89 % of roads, and drains and culverts are under-mapped.
- River distance is planar, not along the flow path. Elevation classes are absolute county-wide bands.
- Step classes: a road at 1,499 m and one at 1,501 m differ by a whole elevation class.
- Rainfall, drainage condition, land cover and catchment area are not in the model.

**Of the software:**

- The sign-in rate limiter is kept per process; run one app process, or add a shared store.
- Signing out ends all sessions of the account (no per-device sign-out).
- State-changing requests rely on `SameSite=Lax` cookies; there is no separate CSRF token.
- Map tiles come from openstreetmap.org, whose usage policy does not allow heavy production use; a deployment
  needs its own tile source or a provider.
- The county-wide map loads in about 3.5 s on a laptop; slower devices will take longer.
- `postgis/postgis` images exist for amd64 only.
- A pipeline run rewrites the provenance records committed in `data/`, which then show as changed in Git.

## 8. Remaining issues

**Not built (in the API contract or design, answering 501 or showing a placeholder):**

| Item | State |
|---|---|
| GeoJSON export (FR-07) | `GET /roads/export` answers 501; no button |
| Admin page: layers, configuration, last run (FR-11 to FR-13) | API reads exist; `/admin` page is a placeholder; saving a configuration answers 501 |
| Second factor at sign-in (FR-09, optional) | not built; `/verify-2fa` page is a placeholder; `ENABLE_2FA` must stay off |
| Changing an account's email address | not built |

**Not verified:**

- Real SMTP delivery (tested only with a stand-in mail server; the development backend was used end to end).
- The browser's real location for "Near me" (the picked-point route was verified; location refusal was simulated).
- A deployment behind HTTPS with `APP_ENV=production`.
- Browsers other than Firefox in the automated tests; manual checks used the development app's built-in browser.

**Decisions still open from the design** (defaults used, see `phase1_requirements_and_design.md` §11): weights (A1),
which road classes are scored (A3), absolute versus percentile elevation classes (A11).

**On this development machine:** an `aquamap_app` container from an older build is still running and published on
all network interfaces (0.0.0.0:8000). The next `docker compose up` in the project folder replaces it with the
current, localhost-only setup.
