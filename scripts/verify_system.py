"""End-to-end check of a running AquaMap: database, API and what the browser shows must agree.

    AQUAMAP_VERIFY_EMAIL=you@example.com AQUAMAP_VERIFY_PASSWORD=... \\
        python scripts/verify_system.py --base-url http://127.0.0.1:8000 --env-file .env --report=verification.md

* Database: read directly with SQL (read-only queries), using the connection settings in ``--env-file``.
* API: called over HTTP after signing in.
* Browser: headless Firefox signs in through the real form and is driven with real clicks; every value is
  read from the page (or from what Leaflet drew) and compared with the database.

Needs an existing, confirmed account (``python -m app.db_tools create-user``), Firefox and geckodriver, and
the development requirements. Nothing is written to the database. Exit status 0 when every check passes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import URL  # noqa: E402

from core import ivi  # noqa: E402

TIER_COLOURS = {"Low": "#56B4E9", "Medium": "#E69F00", "High": "#D55E00", "Severe": "#332288"}
VIEWS = {  # name: (lat, lon, zoom); the first is the dashboard's own start view, the whole city
    "County overview": (-1.286, 36.82, 12),
    "CBD": (-1.2864, 36.8235, 16),
    "Eastlands": (-1.2830, 36.8800, 15),
    "Embakasi": (-1.3180, 36.8950, 15),
    "Westlands": (-1.2660, 36.8050, 15),
}


# ---------------------------------------------------------------- results
@dataclass
class Check:
    area: str
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Results:
    checks: list[Check] = field(default_factory=list)

    def add(self, area: str, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(area, name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {area}: {name}" + (f" - {detail}" if detail else ""), flush=True)
        return bool(ok)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def markdown(self, base_url: str) -> str:
        lines = ["# AquaMap end-to-end verification", "",
                 f"Run: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} against `{base_url}`", "",
                 f"**{len(self.checks) - len(self.failed)} passed · {len(self.failed)} failed** (of {len(self.checks)} checks)", "",
                 "| Area | Check | Result | Detail |", "|---|---|---|---|"]
        for c in self.checks:
            detail = c.detail.replace("|", "\\|")
            lines.append(f"| {c.area} | {c.name} | {'pass' if c.ok else '**FAIL**'} | {detail} |")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- database
def database_engine(env_file: Path):
    from dotenv import dotenv_values

    v = dotenv_values(env_file)
    url = URL.create("postgresql+psycopg", username=v.get("POSTGRES_USER"), password=v.get("POSTGRES_PASSWORD"),
                     host=v.get("POSTGRES_HOST") or "localhost", port=int(v.get("POSTGRES_PORT") or 5433),
                     database=v.get("POSTGRES_DB"))
    # Read-only: every transaction of this engine is READ ONLY, so a mistake here cannot change data.
    return create_engine(url, execution_options={"postgresql_readonly": True}, pool_pre_ping=True)


def sql(engine, query: str, **params) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(query), params)]


# ---------------------------------------------------------------- HTTP (API)
class Api:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=60) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                return error.code, json.loads(payload or b"null")
            except json.JSONDecodeError:
                return error.code, payload.decode(errors="replace")


# ---------------------------------------------------------------- browser
def start_browser():
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service

    driver_path = shutil.which("geckodriver")
    if not (shutil.which("firefox") and driver_path):
        raise SystemExit("Firefox and geckodriver are needed")
    logging.getLogger("selenium.webdriver.common.service").setLevel(logging.CRITICAL)
    scratch = Path.home() / ".cache" / "aquamap-e2e-tmp"   # snap Firefox cannot read /tmp
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(scratch)
    options = Options()
    options.add_argument("-headless")
    options.add_argument("--width=1400")
    options.add_argument("--height=950")
    # No map tiles or other outside requests: only the application under test is reached.
    for key, value in {"network.proxy.type": 1, "network.proxy.http": "127.0.0.1", "network.proxy.http_port": 9,
                       "network.proxy.ssl": "127.0.0.1", "network.proxy.ssl_port": 9,
                       "network.proxy.no_proxies_on": "localhost, 127.0.0.1",
                       "network.proxy.allow_hijacking_localhost": False}.items():
        options.set_preference(key, value)
    driver = webdriver.Firefox(options=options, service=Service(driver_path))
    driver.set_script_timeout(60)
    return driver


def wait(driver, condition: str, timeout: float = 30.0) -> None:
    from selenium.webdriver.support.ui import WebDriverWait

    WebDriverWait(driver, timeout, poll_frequency=0.1).until(lambda d: d.execute_script(f"return Boolean({condition});"))


def settle(driver, quiet: float = 0.8, timeout: float = 60.0) -> None:
    """Wait until the map is not animating or loading and what is drawn stays the same for ``quiet`` seconds."""
    snapshot = ("const r = window.aquamap.roads, m = window.aquamap.map;"
                "return JSON.stringify([r.loading, Boolean(m._animatingZoom), r.drawn.total, r.drawn.matching,"
                " Boolean(document.querySelector('#stats .spinner')), m.getZoom(), m.getCenter()]);")
    deadline, last, since = time.time() + timeout, None, time.time()
    while time.time() < deadline:
        state = driver.execute_script(snapshot)
        if state != last:
            last, since = state, time.time()
        elif state.startswith("[false,false,") and time.time() - since >= quiet:
            return
        time.sleep(0.05)
    raise RuntimeError(f"the dashboard did not settle: {last}")


def number(textvalue: str) -> float:
    return float(re.sub(r"[^0-9.\-]", "", textvalue))


def metres(textvalue: str) -> float:
    value = number(textvalue)
    return value * 1000 if textvalue.strip().endswith("km") else value


# ---------------------------------------------------------------- checks
def check_database(engine, r: Results) -> dict:
    counts = sql(engine, """
        SELECT (SELECT count(*) FROM road_segments) AS roads, (SELECT count(*) FROM river_centerlines) AS rivers,
               (SELECT count(*) FROM road_vulnerability) AS results,
               (SELECT count(*) FROM road_vulnerability WHERE vulnerability_index IS NOT NULL) AS scored,
               (SELECT count(*) FROM road_segments WHERE NOT ST_IsValid(geom) OR ST_SRID(geom) <> 32737) AS bad_geometry""")[0]
    r.add("Database", "roads, rivers and results stored", counts["roads"] > 0 and counts["rivers"] > 0 and counts["scored"] > 0,
          f"{counts['roads']:,} roads, {counts['rivers']:,} rivers, {counts['results']:,} results ({counts['scored']:,} with an IVI)")
    r.add("Database", "every geometry valid, EPSG:32737", counts["bad_geometry"] == 0, f"{counts['bad_geometry']} invalid")

    run = sql(engine, "SELECT run_id, status, model_version, config_version, segments_scored FROM pipeline_runs ORDER BY run_id DESC LIMIT 1")
    ok = bool(run) and run[0]["status"] == "succeeded" and run[0]["segments_scored"] == counts["scored"]
    r.add("Database", "latest pipeline run succeeded and matches the stored results", ok,
          json.dumps(run[0], default=str) if run else "no run recorded")

    cfg = sql(engine, "SELECT config_version, weights, breakpoints, surface_map, tier_thresholds FROM scoring_config WHERE is_active")[0]
    config = ivi.ScoringConfig.from_storage(cfg["weights"], cfg["breakpoints"], cfg["surface_map"], cfg["tier_thresholds"])
    mismatch = sql(engine, """
        SELECT count(*) FILTER (WHERE v.vulnerability_index IS NOT NULL AND abs(v.vulnerability_index -
                 round((:we * score_elevation + :ws * score_slope + :wd * score_river + :wm * score_surface)::numeric, 3)) > 0.001) AS formula,
               count(*) FILTER (WHERE v.risk_category IS DISTINCT FROM ivi_tier(v.vulnerability_index, c.tier_thresholds)) AS tier,
               count(*) FILTER (WHERE v.config_version <> :cfg) AS other_config
        FROM road_vulnerability v JOIN scoring_config c USING (config_version)""",
                   we=config.weights["elevation"], ws=config.weights["slope"], wd=config.weights["river"],
                   wm=config.weights["surface"], cfg=cfg["config_version"])[0]
    r.add("Database", "every stored IVI = weighted sum of its factor scores; every category matches its IVI",
          not any(mismatch.values()), json.dumps(mismatch))

    # Independent re-computation in Python from the stored measurements, for a random sample.
    sample = sql(engine, """SELECT v.road_id, v.elevation_m, v.slope_pct, v.river_dist_m, r.surface, v.vulnerability_index,
                                   v.risk_category, v.score_elevation, v.score_slope, v.score_river, v.score_surface
                            FROM road_vulnerability v JOIN road_segments r USING (road_id) ORDER BY random() LIMIT 2000""")
    wrong = 0
    for row in sample:
        s = ivi.score_segment(row["elevation_m"], row["slope_pct"], row["river_dist_m"], row["surface"], config)
        stored = (float(row["vulnerability_index"]) if row["vulnerability_index"] is not None else None, row["risk_category"],
                  row["score_elevation"], row["score_slope"], row["score_river"], row["score_surface"])
        if (s.ivi, s.tier, s.score_elevation, s.score_slope, s.score_river, s.score_surface) != stored:
            wrong += 1
    r.add("Database", "scoring re-done in Python from the stored measurements gives the stored results", wrong == 0,
          f"{len(sample):,} random roads, {wrong} differ")

    distance = sql(engine, """
        SELECT count(*) AS checked, max(abs(v.river_dist_m - d.dist)) AS worst FROM (
            SELECT road_id, river_dist_m FROM road_vulnerability WHERE river_dist_m IS NOT NULL ORDER BY random() LIMIT 500) v
        CROSS JOIN LATERAL (SELECT ST_Distance(r.geom, c.geom) AS dist FROM road_segments r, river_centerlines c
                            WHERE r.road_id = v.road_id ORDER BY r.geom <-> c.geom LIMIT 1) d""")[0]
    r.add("Database", "stored river distance = PostGIS nearest-river distance", (distance["worst"] or 0) < 0.01,
          f"{distance['checked']} random roads, worst difference {float(distance['worst'] or 0):.6f} m (stored as REAL)")
    return {"config": config, "counts": counts}


def check_api(api: Api, engine, r: Results, email: str, password: str) -> None:
    protected = ["/auth/me", "/roads?bbox=36.80,-1.30,36.84,-1.27", "/roads/search?q=road", "/roads/1", "/roads/summary",
                 "/roads/nearby?lat=-1.29&lon=36.82", "/meta/config", "/analysis/metadata", "/admin/layers"]
    anonymous = Api(api.base.removesuffix("/api/v1"))
    statuses = {path: anonymous.call("GET", path)[0] for path in protected}
    r.add("Access", "every protected endpoint refuses anonymous requests (401)", set(statuses.values()) == {401},
          ", ".join(f"{p.split('?')[0]}={s}" for p, s in statuses.items()))
    status, _ = api.call("POST", "/auth/login", {"email": email, "password": password + "x"})
    r.add("Access", "a wrong password is refused", status == 401, f"HTTP {status}")
    status, body = api.call("POST", "/auth/login", {"email": email, "password": password})
    if not r.add("Access", "sign-in with the account", status == 200, f"HTTP {status}"):
        raise SystemExit("cannot continue without signing in")
    status, _ = api.call("GET", "/admin/layers")
    role = body["user"]["role"]
    r.add("Access", "administrator routes follow the role", status == (200 if role == "admin" else 403), f"role {role}, HTTP {status}")

    status, summary = api.call("GET", "/roads/summary")
    db = {row["tier"]: row for row in sql(engine, """
        SELECT v.risk_category AS tier, count(*) AS segments, sum(r.length_m) / 1000 AS km
        FROM road_segments r LEFT JOIN road_vulnerability v USING (road_id) GROUP BY v.risk_category""")}
    ok = status == 200 and all(t["segments"] == db.get(t["tier"], {}).get("segments", 0)
                               and abs(t["length_km"] - float(db.get(t["tier"], {}).get("km", 0))) < 0.002 for t in summary["by_tier"])
    ok = ok and summary["unscored_segments"] == db.get(None, {}).get("segments", 0)
    r.add("API", "statistics equal the database", ok, ", ".join(f"{t['tier']} {t['segments']:,}" for t in summary["by_tier"]))

    sample = sql(engine, """SELECT road_id FROM road_segments ORDER BY random() LIMIT 40""")
    differences = []
    for row in sample:
        status, feature = api.call("GET", f"/roads/{row['road_id']}")
        expected = sql(engine, """SELECT r.name, r.highway_type, r.surface, v.vulnerability_index, v.risk_category, v.elevation_m,
                                         v.slope_pct, v.river_dist_m FROM road_segments r LEFT JOIN road_vulnerability v USING (road_id)
                                  WHERE r.road_id = :id""", id=row["road_id"])[0]
        p = feature["properties"]
        got = (p["name"], p["highway_type"], p["surface"], p["vulnerability_index"], p["risk_category"], p["elevation_m"],
               p["slope_pct"], p["river_dist_m"])
        want = (expected["name"], expected["highway_type"], expected["surface"],
                float(expected["vulnerability_index"]) if expected["vulnerability_index"] is not None else None,
                expected["risk_category"], expected["elevation_m"], expected["slope_pct"], expected["river_dist_m"])
        if status != 200 or got != want:
            differences.append(row["road_id"])
    r.add("API", "road details equal the database", not differences, f"{len(sample)} random roads, differing: {differences or 'none'}")


def sign_in(driver, base: str, email: str, password: str) -> None:
    from selenium.webdriver.common.by import By

    driver.get(base + "/login")
    driver.find_element(By.ID, "email").send_keys(email)
    driver.find_element(By.ID, "password").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
    wait(driver, "location.pathname === '/' && window.aquamap", 60)
    settle(driver)


def check_frontend(driver, engine, r: Results, base: str, samples: int) -> None:
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By

    # ---- statistics strip
    cards = driver.execute_script("return [...document.querySelectorAll('#stats .stat')].map((c) => "
                                  "[c.querySelector('.label').innerText, c.querySelector('.value').innerText, c.querySelector('.sub').innerText]);")
    db = {row["tier"]: row for row in sql(engine, """SELECT v.risk_category AS tier, count(*) AS segments, sum(r.length_m) / 1000 AS km
        FROM road_segments r JOIN road_vulnerability v USING (road_id) WHERE v.risk_category IS NOT NULL GROUP BY 1""")}
    total_km = sum(float(x["km"]) for x in db.values())
    ok = abs(number(cards[0][1]) - total_km) <= (0.5 if total_km >= 100 else 0.05)
    ok = ok and int(number(cards[0][2])) == sum(x["segments"] for x in db.values())
    for label, value, sub in cards[1:]:
        tier = label.title()
        km = float(db.get(tier, {}).get("km", 0))
        ok = ok and abs(number(value) - km) <= (0.5 if km >= 100 else 0.05)
        ok = ok and int(number(sub.split("·")[1])) == db.get(tier, {}).get("segments", 0)
    r.add("Dashboard", "statistics shown equal the database", ok, "; ".join(f"{c[0].title()} {c[1]}" for c in cards))

    # ---- legend
    thresholds = sql(engine, "SELECT tier_thresholds FROM scoring_config WHERE is_active")[0]["tier_thresholds"]
    legend = driver.execute_script("return [...document.querySelectorAll('#legend li')].map((li) => li.innerText.replace(/\\s+/g, ' ').trim());")
    expected = [f"Severe {thresholds['Severe']:.1f} – 5.0", f"High {thresholds['High']:.1f} – below {thresholds['Severe']:.1f}",
                f"Medium {thresholds['Medium']:.1f} – below {thresholds['High']:.1f}", f"Low 1.0 – below {thresholds['Medium']:.1f}"]
    r.add("Dashboard", "legend shows the stored category thresholds", legend == expected, " | ".join(legend))

    # ---- map: what is drawn, in which category, against the database
    for view, (lat, lon, zoom) in VIEWS.items():
        driver.execute_script("window.aquamap.map.setView([arguments[0], arguments[1]], arguments[2], {animate: false});", lat, lon, zoom)
        settle(driver)
        bbox = driver.execute_script("const b = window.aquamap.map.getBounds();"
                                     "return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((n) => Number(n.toFixed(5)));")
        drawn = driver.execute_script(
            "const out = {}; window.aquamap.roads.layer.eachLayer((g) => g.eachLayer((l) => {"
            " out[l.feature.properties.id] = [l.feature.properties.risk_category, l.options.color]; })); return out;")
        rows = sql(engine, """SELECT r.road_id, v.risk_category FROM road_segments r JOIN road_vulnerability v USING (road_id)
            WHERE v.risk_category IS NOT NULL AND r.geom && ST_Transform(ST_MakeEnvelope(:w, :s, :e, :n, 4326), 32737)
              AND ST_Intersects(r.geom, ST_Transform(ST_MakeEnvelope(:w, :s, :e, :n, 4326), 32737))""",
                   w=bbox[0], s=bbox[1], e=bbox[2], n=bbox[3])
        expected_map = {str(row["road_id"]): row["risk_category"] for row in rows}
        shown = {k: v[0] for k, v in drawn.items()}
        colours_ok = all(colour == TIER_COLOURS[category] for category, colour in drawn.values())
        counts = {t: sum(1 for c in shown.values() if c == t) for t in TIER_COLOURS}
        r.add("Map", f"{view} (zoom {zoom}): drawn roads and their categories equal the database",
              shown == expected_map and colours_ok and len(shown) > 0,
              f"{len(shown):,} drawn, {len(expected_map):,} in the database; " + ", ".join(f"{t} {n}" for t, n in counts.items()))
        painted = driver.execute_script("const c = document.querySelector('#map .leaflet-overlay-pane canvas');"
                                        "const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;"
                                        "let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n;")
        r.add("Map", f"{view}: roads are painted on the map canvas", painted > 1000, f"{painted:,} painted pixels")

    # ---- road panel after a real click, against the database
    by_tier = {t: sql(engine, """SELECT r.road_id FROM road_segments r JOIN road_vulnerability v USING (road_id)
                                  WHERE v.risk_category = :t AND r.length_m > 60 ORDER BY random() LIMIT :n""", t=t, n=max(1, samples // 4))
               for t in TIER_COLOURS}
    chosen = [row["road_id"] for rows in by_tier.values() for row in rows]
    unscored = sql(engine, """SELECT r.road_id FROM road_segments r LEFT JOIN road_vulnerability v USING (road_id)
                              WHERE v.road_id IS NULL ORDER BY random() LIMIT 2""")
    matched, other_road, problems = 0, 0, []
    for road_id in chosen:
        mid = sql(engine, "SELECT ST_Y(p) AS lat, ST_X(p) AS lon FROM (SELECT ST_Transform(ST_LineInterpolatePoint(geom, 0.5), 4326) AS p "
                          "FROM road_segments WHERE road_id = :id) x", id=road_id)[0]
        driver.execute_script("if (!document.getElementById('road-panel').hidden) window.aquamap.panel.close();"
                              "window.aquamap.map.setView([arguments[0], arguments[1]], 18, {animate: false});", mid["lat"], mid["lon"])
        settle(driver)
        offset = driver.execute_script(
            "const p = window.aquamap.map.latLngToContainerPoint(L.latLng(arguments[0], arguments[1]));"
            "const b = document.getElementById('map').getBoundingClientRect(); return [p.x - b.width / 2, p.y - b.height / 2];",
            mid["lat"], mid["lon"])
        ActionChains(driver).move_to_element_with_offset(driver.find_element(By.ID, "map"), round(offset[0]), round(offset[1])).click().perform()
        try:
            wait(driver, "window.aquamap.panel.currentId && document.querySelector('#road-panel dl.facts')", 20)
        except Exception:  # noqa: BLE001 - recorded as a problem
            problems.append(f"{road_id}: click opened nothing")
            continue
        opened = driver.execute_script("return window.aquamap.panel.currentId;")
        if opened != road_id:
            other_road += 1   # a neighbouring road drawn on top at that pixel; still compare what is shown with the database
        panel = driver.execute_script(
            "const out = {}; document.querySelectorAll('#road-panel dl.facts dt').forEach((dt, i) => {"
            " out[dt.innerText] = document.querySelectorAll('#road-panel dl.facts dd')[i].innerText; });"
            "out.title = document.getElementById('road-title').innerText; return out;")
        row = sql(engine, """SELECT r.name, r.highway_type, r.surface, r.length_m, v.vulnerability_index, v.risk_category, v.elevation_m,
                                    v.slope_pct, v.river_dist_m FROM road_segments r LEFT JOIN road_vulnerability v USING (road_id)
                             WHERE r.road_id = :id""", id=opened)[0]
        issues = []
        if panel["title"] != (row["name"] or "Unnamed road"):
            issues.append("name")
        if panel["Road classification"].lower().replace(" ", "_") != row["highway_type"].lower():
            issues.append("class")
        if panel["Surface type"] != ("Not recorded" if row["surface"] is None else panel["Surface type"]) or \
                (row["surface"] is not None and panel["Surface type"].lower().replace(" ", "_").replace(":_", ":") != row["surface"].lower()):
            issues.append("surface")
        if row["vulnerability_index"] is None:
            issues += [] if panel["IVI score"] == "None" else ["ivi"]
        else:
            if abs(number(panel["IVI score"]) - float(row["vulnerability_index"])) > 0.0051:
                issues.append("ivi")
            if panel["Vulnerability category"] != row["risk_category"]:
                issues.append("category")
            if abs(number(panel["Elevation"]) - row["elevation_m"]) > 0.51:
                issues.append("elevation")
            if abs(number(panel["Slope"]) - row["slope_pct"]) > 0.051:
                issues.append("slope")
            tolerance = 50.1 if panel["Distance from nearest river"].endswith("km") else 0.51
            if abs(metres(panel["Distance from nearest river"]) - row["river_dist_m"]) > tolerance:
                issues.append("river distance")
        if issues:
            problems.append(f"{opened}: {', '.join(issues)}")
        else:
            matched += 1
    r.add("Road panel", "values shown after a real map click equal the database (name, class, surface, IVI, category, "
          "elevation, slope, river distance)", not problems and matched == len(chosen),
          f"{matched}/{len(chosen)} roads across all four categories match"
          + (f" ({other_road} clicks hit an overlapping neighbour, also checked)" if other_road else "")
          + (f"; problems: {problems}" if problems else ""))

    for row in unscored:
        driver.execute_script("window.aquamap.panel.open(arguments[0]);", row["road_id"])
        wait(driver, "document.querySelector('#road-panel dl.facts')", 20)
        shown = driver.execute_script("return document.querySelector('#road-panel dl.facts').innerText;")
        r.add("Road panel", f"road {row['road_id']} without a result says so", "IVI score\nNone" in shown.replace("\t", "\n"),
              "IVI and category shown as None")
    driver.execute_script("window.aquamap.panel.close();")

    # ---- search
    for query in ("ngong", "mombasa", "bypass", "kenyatta"):
        box = driver.find_element(By.ID, "search")
        box.clear()
        box.send_keys(query)
        # The previous query's results stay on screen during the 300 ms typing pause: wait until every listed
        # name contains the new text (search matches inside names), or the page says nothing matches.
        try:
            wait(driver, f"(() => {{ const names = [...document.querySelectorAll('#search-results .title span:first-child')]"
                         f".map((e) => e.innerText.toLowerCase()); return (names.length > 0 && names.every((n) => n.includes('{query}')))"
                         f" || document.getElementById('search-state').innerText.includes('No road'); }})()", 20)
        except Exception:  # noqa: BLE001
            r.add("Search", f"'{query}'", False, "no answer")
            continue
        shown = driver.execute_script("return [...document.querySelectorAll('#search-results .title span:first-child')].map((e) => e.innerText);")
        expected = [row["name"] for row in sql(engine, """
            SELECT min(name) AS name FROM road_segments WHERE lower(name) LIKE :c GROUP BY lower(name)
            ORDER BY bool_or(lower(name) LIKE :p) DESC, lower(name) LIMIT 10""", c=f"%{query}%", p=f"{query}%")]
        r.add("Search", f"'{query}': names listed equal the database", shown == expected, f"{len(shown)} shown: {shown[:4]}…")
    driver.find_element(By.ID, "search").clear()

    # ---- filters: category + class + IVI range at one view
    lat, lon, zoom = VIEWS["Eastlands"]
    driver.execute_script("window.aquamap.map.setView([arguments[0], arguments[1]], arguments[2], {animate: false});"
                          "for (const t of ['Low', 'Medium']) document.querySelector(`#tier-filters input[value=\"${t}\"]`).click();"
                          "document.getElementById('classes-none').click();"
                          "document.querySelector('#class-filters input[value=\"residential\"]').click();"
                          "const s = document.getElementById('ivi-min'); s.value = '3.2'; s.dispatchEvent(new Event('input'));", lat, lon, zoom)
    time.sleep(0.5)
    settle(driver)
    bbox = driver.execute_script("const b = window.aquamap.map.getBounds();"
                                 "return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((n) => Number(n.toFixed(5)));")
    drawn_ids = sorted(driver.execute_script("return window.aquamap.roads.drawn.ids;"))
    expected_ids = sorted(row["road_id"] for row in sql(engine, """
        SELECT r.road_id FROM road_segments r JOIN road_vulnerability v USING (road_id)
        WHERE v.risk_category IN ('High', 'Severe') AND r.highway_type = 'residential' AND v.vulnerability_index >= 3.2
          AND r.geom && ST_Transform(ST_MakeEnvelope(:w, :s, :e, :n, 4326), 32737)
          AND ST_Intersects(r.geom, ST_Transform(ST_MakeEnvelope(:w, :s, :e, :n, 4326), 32737))""",
        w=bbox[0], s=bbox[1], e=bbox[2], n=bbox[3]))
    r.add("Filters", "High+Severe, residential, IVI >= 3.2: drawn roads equal the database",
          drawn_ids == expected_ids and len(expected_ids) > 0, f"{len(drawn_ids)} drawn, {len(expected_ids)} expected")
    driver.execute_script("document.getElementById('reset-filters').click();")
    settle(driver)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/verify_system.py", description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help="database connection settings (POSTGRES_*)")
    parser.add_argument("--samples", type=int, default=24, help="roads clicked on the map (spread over the four categories)")
    parser.add_argument("--report", type=Path, help="also write the results as Markdown")
    args = parser.parse_args(argv)
    email, password = os.environ.get("AQUAMAP_VERIFY_EMAIL"), os.environ.get("AQUAMAP_VERIFY_PASSWORD")
    if not email or not password:
        parser.error("set AQUAMAP_VERIFY_EMAIL and AQUAMAP_VERIFY_PASSWORD (never pass a password as an argument)")
    random.seed()
    results = Results()
    engine = database_engine(args.env_file)
    print("Database")
    check_database(engine, results)
    print("API and access")
    check_api(Api(args.base_url), engine, results, email, password)
    print("Browser")
    driver = start_browser()
    try:
        sign_in(driver, args.base_url, email, password)
        results.add("Dashboard", "sign-in through the form opens the dashboard", True)
        check_frontend(driver, engine, results, args.base_url, args.samples)
        driver.find_element("id", "sign-out").click()
        wait(driver, "location.pathname === '/login'", 20)
        driver.get(args.base_url + "/")
        wait(driver, "location.pathname === '/login'", 20)
        results.add("Access", "after signing out the dashboard sends the browser back to sign-in", True)
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass
    print(f"\n{len(results.checks) - len(results.failed)} passed, {len(results.failed)} failed")
    if args.report:
        args.report.write_text(results.markdown(args.base_url), encoding="utf-8")
        print(f"report written to {args.report}")
    return 1 if results.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
