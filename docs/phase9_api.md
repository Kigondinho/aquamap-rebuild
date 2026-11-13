# Phase 9 — REST API

Base path `/api/v1`. Interactive documentation: `http://localhost:8000/docs` (off when `APP_ENV=production`).
Machine-readable description: [`openapi.json`](openapi.json), regenerated with `python -m app.openapi_export`; a test
fails if that file no longer matches the application.

```bash
uvicorn app.main:app --reload
python -m app.db_tools create-user --email you@example.com --name "Your Name" --admin
```

## Endpoints

Access: **P** public, **U** signed-in user, **A** administrator.

| Need | Method and path | Access | Notes |
|---|---|---|---|
| Application health | GET `/health` | P | Always 200; `status` is `ok` or `degraded` (database unreachable) |
| Registration | POST `/auth/register` | P | 201; creates a normal user; does not sign in; 409 if the email exists |
| Sign in | POST `/auth/login` | P | Sets the session cookie; 401 on bad credentials; 429 after repeated failures |
| Sign out | POST `/auth/logout` | P | Clears the cookie |
| Current user | GET `/auth/me` | U | `user_id`, `email`, `full_name`, `role` |
| Road retrieval and filtering | GET `/roads` | U | `bbox` (required), `tier`, `highway`, `min_ivi`, `max_ivi`, `limit`, `offset`, `zoom` |
| Road search | GET `/roads/search` | U | `q` (2–100 characters), `limit`, `offset`; one result per name |
| Nearby roads | GET `/roads/nearby` | U | `lat`, `lon`, `radius_m` (≤ 2,000), filters, `limit`, `offset`; nearest first with `distance_m` |
| Road details | GET `/roads/{road_id}` | U | Measurements, factor scores, weights, flags, model and analysis date; 404 if unknown |
| Vulnerability statistics | GET `/roads/summary` | U | Segments and km in total, per category and unscored; optional `bbox` and filters |
| Analysis metadata | GET `/analysis/metadata` | U | Model and configuration versions, analysis date, run, factor definitions, category ranges, coverage, flags, data sources, and the statement that the index is not a flood probability |
| Legend metadata | GET `/meta/config` | U | Category ranges, boundary rule, weights |
| Layers, active configuration, last run | GET `/admin/layers`, `/admin/config`, `/admin/pipeline/status` | A | Read-only |

Not built (they answer 501): GET `/roads/export`, PUT `/admin/config`, POST `/auth/verify-2fa` and `/auth/resend-2fa`.
With `ENABLE_2FA=true`, sign-in answers 501 instead of silently skipping the second step; leave it off.

## Authentication and protection

- **Passwords** are stored only as Argon2id hashes with a random salt. Rules: 8–128 characters with at least one letter
  and one digit; the confirmation must match. A password is never returned, logged or echoed in an error.
- **Session** is a signed token (HS256, key `SECRET_KEY`) in a cookie that is `HttpOnly` (scripts cannot read it),
  `SameSite=Lax` (not sent on cross-site POSTs) and `Secure` in production. Lifetime `SESSION_TTL_HOURS` (12 h).
  The token is never in a response body.
- **Every request re-reads the account.** The role and active state come from the database, not the token, so
  disabling an account or changing a role takes effect on the next request.
- **Sign-in failures** give one message ("Incorrect email or password.") for an unknown email, a wrong password and a
  disabled account, and take about the same time. Five failures for the same email from the same address within 15
  minutes block further attempts with 429 and a `Retry-After` header.
- **Roles.** Registration always creates a `user`; extra fields such as `role` are rejected. Administrators are
  created from the command line only. Admin routes answer 403 to signed-in non-administrators.
- **Pages.** `/` redirects to `/login` without a session; `/admin` redirects non-administrators to `/`.

## Request validation

Every parameter has a type and limits declared in the route and shown in the OpenAPI document; anything outside them
is a 422 before any query runs. Examples: `bbox` must be four finite numbers in lon/lat order that overlap the county;
`tier` only Low, Medium, High, Severe; `min_ivi` and `max_ivi` between 1 and 5 and in order; `highway` 1–50 letters,
digits, `_`, `:` or `-`; `radius_m` above 0 and at most 2,000; `road_id` a positive integer. All SQL uses bound
parameters; request values are never placed in SQL text.

## Pagination and response size

Lists take `limit` and `offset` and always return:

```json
"pagination": {"limit": 1000, "offset": 0, "returned": 1000, "total": 9978, "has_more": true, "next_offset": 1000}
```

Ask for the next page with `offset = next_offset`; it is `null` on the last page. The order is fixed (highest index
first for `/roads`, nearest first for `/roads/nearby`, best match first for search), so pages never overlap or skip.
A page beyond the end is empty and still reports the total.

| Endpoint | Default page | Largest page |
|---|---|---|
| `/roads` | 1,000 | 5,000 |
| `/roads/search` | 10 | 50 |
| `/roads/nearby` | 20 | 500 |

Other limits on size: list items carry five properties only (full detail is one road at a time); the bounding box is
clipped to the county; `zoom` below 15 simplifies geometry; responses over 2 kB are gzip-compressed when the client
accepts it.

## Responses

- **Geometry** is GeoJSON in EPSG:4326, coordinates as `[longitude, latitude]` with six decimals: a `Feature` for one
  road and a `FeatureCollection` for lists, which Leaflet's `L.geoJSON` reads directly.
- **Errors** always have the same body, `{"detail": "One sentence."}`. Validation errors add
  `"errors": [{"field": "bbox", "message": "..."}]`.

| Status | Meaning |
|---|---|
| 401 | Not signed in, or the session is expired, altered or belongs to a disabled account |
| 403 | Signed in, but not an administrator |
| 404 | Unknown road or route |
| 409 | Email already registered |
| 422 | A parameter or the body is invalid |
| 429 | Too many failed sign-in attempts |
| 500 | Unexpected error; logged on the server, no internals in the response |
| 501 | Part of the contract that is not built |
| 503 | The database cannot be reached |

## Verification

Tests (747 in total with a database; 466 run without one):

- `tests/db/test_api_endpoints.py`: each endpoint over HTTP with real sign-in cookies against PostGIS: registration,
  sign-in, lock-out, session expiry and tampering, immediate loss of access for disabled accounts, role changes,
  401/403 on every restricted route, pagination walked page by page, every filter, invalid parameters, GeoJSON
  coordinate order, error shape, pages.
- `tests/api/test_openapi.py`: the generated document: every operation has a summary and a typed response; restricted
  operations declare the session cookie and 401; all errors use one schema; list operations are paginated; parameter
  limits are present; no password field in any response schema; `docs/openapi.json` is current.
- `tests/api/test_routes.py`, `tests/unit/test_auth_units.py`: behaviour without a database (401 first, 503 on outage),
  password hashing, token forgery cases, the limiter, request validation.

Run against the real server and data (52,471 roads):

| Request | Result | Size | Time |
|---|---|---|---|
| Roads, city centre, default page | 1,000 of 9,978 | 344 kB before compression | 111 ms |
| Roads, whole county, largest page, zoom 11 | 5,000 of 52,468 | 1.2 MB before compression | 453 ms |
| Severe primary and trunk roads | 18 | 15 kB | 40 ms |
| Search "bypass" | 7 names | 42 kB | 7 ms |
| Nearby, 500 m of the CBD | 20 of 311 | 8 kB | 7 ms |
| Detail, statistics, analysis metadata | | 1–3 kB | 4–51 ms |

## Limitations

- The sign-in limiter is kept in the process: it resets on restart and is not shared between worker processes.
- Signing out clears the cookie; a copied token stays valid until it expires (there is no server-side session list).
  Disabling the account ends it at once.
- No password reset or change, no email verification and no second factor.
- Requests that change state are JSON POSTs protected by `SameSite=Lax`; there is no separate CSRF token.
- `offset` pagination re-reads earlier rows, so very deep pages of the whole county get slower.
