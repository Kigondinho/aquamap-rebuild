# Phase 12 — Automated testing

```bash
pip install -r requirements-dev.txt          # pytest and Selenium
pytest                                       # quick: everything that needs no database
pytest --test-db                             # complete: also database, pipeline and browser tests
pytest --test-db --report=docs/test-report.md  # the same, and save the report as Markdown (one word, with =)
```

Every run ends with a report of **passed**, **failed** and **unverified** tests by area (`tests/report.py`):

| Status | Meaning |
|---|---|
| Passed | The test ran and every check held |
| Failed | The test ran and a check did not hold, or its setup failed |
| Unverified | The test did not run (skipped), so it proves nothing either way; the reason is listed |

The latest complete run is in [`phase12_test_report.md`](phase12_test_report.md).

## A separate test database

Tests never use the development or production database.

- `pytest --test-db` takes the server and account from `.env` and uses the database **`<POSTGRES_DB>_test`**
  (`aquamap_db_test`), creating it if needed. `AQUAMAP_TEST_DATABASE_URL` may name another one.
- **Only a database whose name ends in `_test` is accepted.** Anything else stops the whole run before a single
  statement is sent, with no override (`tests/testdb.py`). The rule is checked again immediately before any table
  is emptied.
- The application's own settings are pointed at a closed port for the whole test session, so code that opens the
  application's normal database connection cannot reach a real database by accident.
- Isolation inside the test database: most tests run in a transaction that is rolled back. The tests that must
  commit (the full pipeline, the browser tests, a rollback check, the create-user command) empty the test database
  before and after, or delete exactly what they created.
- Without `--test-db`, database and browser tests are skipped and reported as unverified, not as passed.

Checked after the complete run: the development database still holds its 52,471 roads, 47,619 results, one
scoring configuration and no users.

## What is tested, against the brief

| Brief | Where | How |
|---|---|---|
| **Unit**: IVI calculation | `tests/unit/test_ivi.py` | Ten hand-calculated values; one class up raises the IVI by exactly that factor's weight; every class combination stays on the 1–5 scale |
| Factor normalisation | same | 28 values at, just below and just above every class boundary; scores never rise with the measurement; impossible values refused |
| Weight validation | same, `test_config_defaults.py` | Baseline weights; sums other than 1, negative, missing or non-finite weights rejected; stored configuration round trip |
| Vulnerability classification | `test_ivi.py`, `test_roads_query_parsing.py`, `tests/db/test_classification.py` | Category boundaries (on a threshold = higher category); the SQL function and the Python function agree on all 4,001 storable values |
| Missing-data handling | `test_ivi.py`, `tests/pipeline/test_compute_metrics.py` | Each missing input gives its documented outcome and flag; nodata samples left out of means; no index without terrain data |
| Geospatial calculations | `tests/pipeline/` | Slope on planes with known slope; reprojection; sampling along lines; river distance from the whole line; geometry repair; known answers on synthetic rasters |
| **Integration**: database connectivity | `tests/db/test_connection.py`, `test_schema*.py` | Connection, PostGIS, EPSG:32737, re-applying the schema, transactions |
| Data ingestion | `tests/db/test_ingestion.py`, `test_pipeline_end_to_end.py` | Load, re-run without duplicates, in-place updates, rollback on failure, verification against the source |
| Spatial queries | `tests/db/test_spatial.py`, `test_road_queries.py` | Bounding box and radius answers equal an independent pyproj + Shapely calculation on 150 random lines; GiST index used |
| API responses | `tests/db/test_api_endpoints.py`, `tests/api/` | Every endpoint over HTTP with real sign-in; GeoJSON in EPSG:4326; pagination; one error shape; generated OpenAPI document |
| Road filtering, road detail | `test_road_queries.py`, `test_api_endpoints.py` | Every filter, inclusive bounds, combinations; detail values equal the stored row |
| Whole pipeline | `tests/db/test_pipeline_end_to_end.py` | Synthetic DEM and roads through `load_vectors.run`, `prepare_dem.run`, `score_roads.run` and the query layer; every index equals the hand calculation; a second run changes nothing |
| **Authentication**: registration, login, invalid credentials | `tests/db/test_auth_flows.py`, `test_api_endpoints.py`, `tests/unit/test_auth_units.py` | Argon2id storage; generic failure messages; lock-out after five failures |
| OTP verification | `test_auth_flows.py`, `tests/unit/test_email_and_otp_units.py` | Right, wrong, used, expired, malformed and other-purpose codes; cancellation after five guesses; codes stored only as keyed hashes |
| Expired sessions, protected endpoints | same | Expired, altered, signed-out and outdated tokens refused; every protected route answers 401 without a valid session and 403 to non-administrators |
| **Frontend**: API integration, search, filtering, map rendering, road detail | `tests/e2e/test_dashboard.py`, `tests/api/test_frontend.py` | See below |

### Frontend in a real browser

`tests/e2e/` starts the real application in its own process on the test database, signs in through the real form
in headless Firefox, and drives the dashboard with real clicks and typing. The data is a small synthetic network
whose scores are computed by the production scoring function, so every expected value is known; each expectation
is also fetched from the API through the browser's own session and compared with what the page shows.

- **API integration:** statistics, legend, filter choices and the signed-in user equal the API's answers.
- **Map rendering:** every scored road in view is drawn, in its category's colour, thickness and dash; the canvas
  pixels at each road are checked to be that colour; zooming out thins the lines; leaving the county shows the notice.
- **Filtering:** category, IVI range and road class change what is drawn to exactly what the API returns for the same
  filters; statistics follow; "select at least one" and reset behave.
- **Search:** results equal the API's; choosing one opens and outlines that road; short text, no match and literal
  `%` work.
- **Road detail:** a real mouse click on a drawn road opens its panel, and every field equals the stored value.
- Also: nearby roads around a picked point, location refused, a failing server with "Try again" recovering, an
  ended session sending the browser to sign-in, sign-out.

Requests to other sites (map tiles) are refused inside the test browser, so the tests need no internet.

## Real code, and the few stand-ins

Tests call the production functions: `core.ivi`, the pipeline's `run()` entry points, the query layer, the FastAPI
application (in-process and as a real server), and the static files in a real browser. Stand-ins are used only
where the real thing is outside the system or the machine:

| Stand-in | Why |
|---|---|
| OpenStreetMap, Nominatim and SRTM downloads (`tests/pipeline/test_acquisition.py`) | External services; the code paths are tested with fixed responses |
| SMTP server object | No mail server in tests; the order STARTTLS → login → send and failure handling are checked |
| Email delivery in account flows | The development "console" backend: the code is read from its outbox, as a user would read the email |
| The browser's location answer | Headless Firefox has no location service |
| Map tiles | Blocked, so no internet is needed |

## Results of the complete run

`pytest --test-db` on 17 November 2026: see [`phase12_test_report.md`](phase12_test_report.md) for the full table.
Every test ran (nothing unverified) and all passed, in under two minutes.

### A real bug found by the new browser tests

After a road had been selected, **no other road on the map could be clicked**: the selection outline was drawn on
a second, transparent map canvas that sat on top of the roads and took every click. The Phase 11 manual check missed
it because the page was reloaded between selections. Fixed in `static/js/map.js`: decorations (the outline and the
nearby circle) are drawn in a layer that ignores the mouse. A regression test now clicks a second road while the
first is outlined, and a road inside the nearby circle. Confirmed again by hand on the real data.

## Not covered

- No continuous integration: the suite runs on demand, not on every push.
- No automated performance test; request times were measured by hand in Phases 8 and 9.
- The browser tests use Firefox only and a small synthetic network, not the full county data.
- The test database lives on the same PostgreSQL server as the development database (separate database, same
  account). A separate server or container would isolate further.
- Real SMTP delivery, real downloads and a phone's real location are not exercised.
