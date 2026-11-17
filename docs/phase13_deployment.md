# Phase 13 — Docker, CI and deployment preparation

Setting up from a clean clone is in the README ("Quick start"). This page explains what runs where, how it is
kept secure and reproducible, and how it was checked.

## What runs

| Service | Image | Starts with | Purpose |
|---|---|---|---|
| `db` | `postgis/postgis:16-3.4`, pinned by digest | `docker compose up` | PostgreSQL 16 + PostGIS 3.4; data in the `pgdata` volume |
| `app` | built from `Dockerfile` | `docker compose up` | FastAPI app and the dashboard on port 8000 |
| `pipeline` | built from `Dockerfile` with `INSTALL_PIPELINE=true` | `docker compose --profile pipeline run --rm pipeline` | Downloads, processes and scores the data, then exits |

Start-up order, so that the app only starts once everything it needs is ready:

1. `db` counts as healthy only when PostgreSQL accepts TCP connections (`pg_isready -h 127.0.0.1`). The temporary
   server PostGIS runs during first-time setup does not, so a half-initialised database is never taken for ready.
2. `app` waits for that, then `docker/entrypoint.sh` creates or upgrades the schema (`python -m app.db_tools init`,
   idempotent, one transaction, retried while the database is still starting), then starts the web server. The
   same happens in the `pipeline` container. A database lock stops two containers running the scripts at once.
3. `app` counts as healthy only when `/api/v1/health` answers **and** reports the database reachable.
   `docker compose up --wait` returns when both services are healthy.

The schema used to be created from a host folder mounted into the database container. That depended on the
folder's file permissions on the host (it failed once in Phase 6) and only ran on an empty volume. Now the app
creates it over its normal connection, and an existing database is upgraded on every start.

### One-command pipeline

`python -m pipeline.run_pipeline` (the `pipeline` service's command) runs, in order: **acquire** (download what
is not on disk yet, clean, validate), **terrain** (DEM and slope), **load** (roads and rivers into PostGIS), and
**score**. It stops at the first failing step with exit status 1. `--from STEP` resumes, `--only STEP` runs one
step. Re-running is safe. The container writes `data/` as your own user (`HOST_UID`/`HOST_GID` from `.env`).

## Configuration and secrets

- **Secrets live only in `.env`**, which is git-ignored and excluded from the Docker build. `python3 scripts/init_env.py`
  creates it from `.env.example` with a random `SECRET_KEY` (64 characters) and `POSTGRES_PASSWORD` (32), readable by
  its owner only, and refuses to overwrite an existing one.
- Compose refuses to start without `POSTGRES_PASSWORD`; the app refuses to start without `SECRET_KEY` and
  `POSTGRES_PASSWORD`, and in production rejects placeholder values, console email and an insecure cookie.
- **Published ports listen on 127.0.0.1 only** (app 8000, database 5433, both configurable). Nothing is reachable
  from other machines unless you put a reverse proxy in front.
- **The image** runs as an unprivileged user and contains only `app/`, `core/`, `pipeline/`, `db/`, `static/` and the
  start-up script: no `.env`, tests, documents, data or Git history.
- CI stores no secrets: its database exists only inside the job and trusts local connections, and the Docker job
  generates its own `.env`.
- Tests check all of this (`tests/test_deployment_config.py`), plus that no `.env`, virtualenv, editor or data file
  is tracked by Git or would be picked up by `git add -A`.

## Reproducibility

| What | How it is fixed |
|---|---|
| Python and Debian in the image | `python:3.12-slim` pinned by digest (Python 3.12.16, Debian 13) |
| Database | `postgis/postgis:16-3.4` pinned by digest, the same in Compose and CI |
| Python packages | Exact versions in `requirements*.txt` |
| System library for the pipeline | `libexpat1` from Debian 13 (security updates may change its patch level) |
| Schema | Idempotent SQL applied on every start |
| Data | Downloads are recorded with checksums in `*.provenance.json`; the pipeline re-uses files already on disk |

### Updating pinned versions

```bash
docker pull python:3.12-slim && docker image inspect python:3.12-slim --format '{{index .RepoDigests 0}}'
docker pull postgis/postgis:16-3.4 && docker image inspect postgis/postgis:16-3.4 --format '{{index .RepoDigests 0}}'
```

Put the new digests in `Dockerfile`, `docker-compose.yml` and `.github/workflows/ci.yml` (a test checks that
Compose and CI use the same database image), then rebuild and run the tests.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main`, every pull request, and on demand.

| Job | Steps |
|---|---|
| `tests` | PostGIS service → Python 3.12 → install `requirements-dev.txt` and `requirements-pipeline.txt` → OpenAPI document up to date → **complete suite** with `--test-db --require-all` (a skipped test fails the run) → report in the job summary and as an artifact |
| `docker` | Create `.env` → `docker compose config` → build app and pipeline images → pipeline libraries load inside the image → `docker compose up --wait` → health and schema check → create an account and sign in through the API → anonymous access refused → restart keeps schema and data → `docker compose down -v` |

## How it was checked

Everything below ran on this machine in a **clean copy**: exactly the files a commit contains, without `.env`,
data, virtualenv or Git history. It used its own Compose project and ports, so the existing development containers
were not touched.

1. `python3 scripts/init_env.py`, then `docker compose up -d --build --wait`: built and healthy in 65 s; the app log
   shows the schema created; `db_tools check` passes inside the container; the container runs as `aquamap` and holds
   only the application files.
2. An account created inside the container; sign-in, `/auth/me` and the analysis metadata work over HTTP; anonymous
   requests get 401.
3. **The pipeline container failed at first.** `rasterio` needs the system library `libexpat`, which the slim base
   image (Debian 13) lacks. Fixed in the `Dockerfile`; the image build now checks the imports, and CI runs them
   inside the image (the CI test job uses Ubuntu's own libraries and would not notice).
4. After the fix, `docker compose --profile pipeline run --rm pipeline` (with the raw downloads already on disk, to
   avoid fetching from public services again) ran all four steps in 22 s: 52,471 roads loaded, 47,619 scored
   (693 Severe, 14,940 High), the same as the development database. Files in `data/` belong to the user.
5. `docker compose down` then `up --wait`: schema, data and a signed-in session survived.
6. **CI test job, reproduced**: a fresh PostGIS container configured like the CI service, a fresh virtualenv installed
   from the requirement files (43 s), then `pytest --test-db --require-all`: **975 passed, 0 failed, 0 unverified**.
   On the way it found that the secrets scan read third-party packages inside a virtualenv not named `.venv`
   (now any virtualenv is skipped, and `.venv*/` is git-ignored).
7. **CI Docker job, reproduced**: every `run` step of the job read from `ci.yml` and executed with GitHub's default
   shell options (only the port and Compose project name changed): **succeeded in 78 s**.

Not checked here: the workflow on GitHub's servers itself (there is no GitHub CLI on this machine). The first push
will run it; look at the repository's Actions tab.

## Deployment notes

This phase prepares a deployment; it does not perform one. For a server reachable by others:

- Set `APP_ENV=production` (cookies become `Secure`, interactive docs are switched off, placeholders are refused).
- Put a reverse proxy with HTTPS in front of `127.0.0.1:${APP_PORT}`; do not publish the app or database ports
  directly.
- Configure SMTP (`SMTP_HOST`, `SMTP_FROM`, ...) or accounts will not need email verification and password reset
  by email is off.
- Run one app process: the sign-in rate limiter is kept in memory per process.
- Back up the `pgdata` volume, or the database, regularly.
- `postgis/postgis` images are published for amd64 only; ARM machines (for example Apple silicon) run them under
  emulation.

## Changes to an existing development setup

Containers no longer have fixed names, so the next `docker compose up` in this folder replaces the old
`aquamap_db` and `aquamap_app` containers with `aquamap-rebuild-db-1` and `aquamap-rebuild-app-1`. The database
volume is kept, so no data is lost. Add `APP_PORT=8000` to an older `.env` if you want to change it; otherwise the
default applies.
