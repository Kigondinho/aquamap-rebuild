"""Browser tests: the real interface, in headless Firefox, against a live server on the test database.

What is real: the application process (``uvicorn app.main:app``), PostGIS, the session cookie, every
API call the page makes, Leaflet drawing on its canvas. What is controlled: the data (a small synthetic
road network whose scores are computed by the production scoring function) and the network (requests
to other sites, such as map tiles, are refused so the tests need no internet).

The road tables of the TEST database are emptied before and after this module. ``tests/testdb.py``
guarantees that this can only ever be a database whose name ends in ``_test``.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.schemas.auth import RegisterRequest
from app.services.auth import register_user
from core import ivi
from core.config_defaults import MODEL_VERSION
from tests import testdb
from tests.db.conftest import ORIGIN_SQL

ROOT = Path(__file__).resolve().parents[2]
CFG = ivi.ScoringConfig()
EMAIL = "browser-test@example.com"
# name, class, surface, metres east of the origin (36.82 E, 1.29 S), elevation m, slope %, river distance m
# ("skip" = no result row at all, as for a road class that is not analysed)
NETWORK = [
    ("Mto Road", "primary", "dirt", 100.0, 1480.0, 1.0, 20.0),            # 5.00 Severe
    ("Bonde Lane", "residential", None, 250.0, 1550.0, 3.0, 100.0),       # 3.85 High
    ("Kati Street", "residential", "asphalt", 400.0, 1650.0, 6.0, 200.0), # 2.85 Medium
    ("kati street", "tertiary", "asphalt", 550.0, 1650.0, 12.0, 400.0),   # 2.35 Medium (same name, second segment)
    ("Juu Avenue", "tertiary", "asphalt", -200.0, 1900.0, 20.0, 2000.0),  # 1.15 Low
    ("Njia 100% Way", "service", None, -350.0, 1750.0, 8.0, 320.0),       # 2.35 Medium
    (None, "footway", None, -100.0, "skip", None, None),                  # not analysed
    ("Giza Road", "residential", None, 700.0, None, None, 150.0),         # analysed, no terrain data: no index
]


@pytest.fixture(scope="module")
def seeded(db_engine: Engine) -> Iterator[dict]:
    """Commit the synthetic network and one confirmed account; remove everything afterwards."""
    testdb.empty(db_engine)
    password = "Bt1-" + secrets.token_urlsafe(12)
    expected: dict[str | None, dict] = {}
    with db_engine.begin() as conn:
        stored = CFG.to_storage()
        cfg = conn.execute(text(
            "INSERT INTO scoring_config (weights, breakpoints, surface_map, tier_thresholds, is_active) VALUES "
            "(CAST(:w AS jsonb), CAST(:b AS jsonb), CAST(:s AS jsonb), CAST(:t AS jsonb), TRUE) RETURNING config_version"),
            {"w": json.dumps(stored["weights"]), "b": json.dumps(stored["breakpoints"]),
             "s": json.dumps(stored["surface_map"]), "t": json.dumps(stored["tier_thresholds"])}).scalar_one()
        run = conn.execute(text("INSERT INTO pipeline_runs (config_version, status, finished_at, model_version) "
                                "VALUES (:c, 'succeeded', now(), :m) RETURNING run_id"), {"c": cfg, "m": MODEL_VERSION}).scalar_one()
        for name, highway, surface, dx, elevation, slope, river in NETWORK:
            road = conn.execute(text(
                f"INSERT INTO road_segments (name, highway_type, surface, osm_id, geom) VALUES (:n, :h, :s, :o, "
                f"ST_MakeLine(ST_Translate({ORIGIN_SQL}, :dx, 0), ST_Translate({ORIGIN_SQL}, :dx, 200))) RETURNING road_id"),
                {"n": name, "h": highway, "s": surface, "o": 900000 + int(dx), "dx": dx}).scalar_one()
            entry = {"id": road, "highway": highway, "surface": surface, "elevation": None, "slope": None,
                     "river": None, "ivi": None, "tier": None}
            expected[name] = entry
            if elevation == "skip":
                continue
            score = ivi.score_segment(elevation, slope, river, surface, CFG)  # the production scoring function
            conn.execute(text(
                "INSERT INTO road_vulnerability (road_id, config_version, run_id, model_version, elevation_m, slope_pct, "
                "river_dist_m, score_elevation, score_slope, score_river, score_surface, vulnerability_index, risk_category, "
                "data_flags, terrain_samples) VALUES (:r, :c, :run, :m, :e, :s, :d, :se, :ss, :sr, :sm, :i, :t, :f, 8)"),
                {"r": road, "c": cfg, "run": run, "m": MODEL_VERSION, "e": elevation, "s": slope, "d": river,
                 "se": score.score_elevation, "ss": score.score_slope, "sr": score.score_river, "sm": score.score_surface,
                 "i": score.ivi, "t": score.tier, "f": list(score.flags)})
            entry.update(elevation=elevation, slope=slope, river=river, ivi=score.ivi, tier=score.tier)
    with Session(db_engine) as session:
        register_user(session, RegisterRequest(full_name="Browser Tester", email=EMAIL, password=password,
                                               confirm_password=password), verified=True)
    yield {"roads": expected, "email": EMAIL, "password": password}
    testdb.empty(db_engine)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def server(database_url: str, seeded: dict) -> Iterator[str]:
    """The real application in its own process, connected to the test database."""
    url = make_url(database_url)
    port = _free_port()
    env = os.environ | {
        "APP_ENV": "test", "SECRET_KEY": secrets.token_urlsafe(48), "EMAIL_BACKEND": "none", "ENABLE_2FA": "false",
        "POSTGRES_HOST": url.host or "localhost", "POSTGRES_PORT": str(url.port or 5432), "POSTGRES_DB": url.database,
        "POSTGRES_USER": url.username or "", "POSTGRES_PASSWORD": url.password or "",
    }
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                               cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while True:
            try:
                with urllib.request.urlopen(f"{base}/api/v1/health", timeout=2) as response:
                    if json.load(response)["database"] is True:
                        break
            except OSError:
                pass
            if process.poll() is not None or time.time() > deadline:
                output = process.stdout.read().decode(errors="replace")[-800:] if process.stdout else ""
                pytest.fail(f"the application did not start on the test database:\n{output}")
            time.sleep(0.3)
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    """Headless Firefox. Skips (reported as unverified) when Selenium, Firefox or geckodriver is missing."""
    selenium = pytest.importorskip("selenium", reason="selenium is not installed (pip install -r requirements-dev.txt)")
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service

    del selenium
    driver_path = shutil.which("geckodriver")
    if not shutil.which("firefox") or not driver_path:
        pytest.skip("Firefox and geckodriver are needed for the browser tests")
    logging.getLogger("selenium.webdriver.common.service").setLevel(logging.CRITICAL)
    # Snap-packaged Firefox cannot read /tmp: keep its profile under the home directory.
    scratch = Path.home() / ".cache" / "aquamap-e2e-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    previous_tmp = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = str(scratch)
    options = Options()
    options.add_argument("-headless")
    options.add_argument("--width=1400")
    options.add_argument("--height=900")
    # Refuse every request that does not go to this machine (map tiles, anything else): no internet needed or used.
    for name, value in {"network.proxy.type": 1, "network.proxy.http": "127.0.0.1", "network.proxy.http_port": 9,
                        "network.proxy.ssl": "127.0.0.1", "network.proxy.ssl_port": 9,
                        "network.proxy.no_proxies_on": "localhost, 127.0.0.1",
                        "network.proxy.allow_hijacking_localhost": False, "geo.enabled": False,
                        "datareporting.policy.dataSubmissionEnabled": False, "app.update.enabled": False}.items():
        options.set_preference(name, value)
    try:
        driver = webdriver.Firefox(options=options, service=Service(driver_path))
    except WebDriverException as exc:
        pytest.skip(f"Firefox could not be started ({str(exc).splitlines()[0][:120]})")
    finally:
        if previous_tmp is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = previous_tmp
    driver.set_script_timeout(20)
    yield driver
    try:
        driver.quit()
    except Exception:  # noqa: BLE001 - shutting down a browser must never fail the run
        pass
