"""Repeatable timing of the AquaMap road queries (report Table 5.7), the GET /roads endpoint and a concurrency run.

    python benchmarks/bench_queries.py                          # service-level queries only (database from .env)
    AQUAMAP_BENCH_EMAIL=... AQUAMAP_BENCH_PASSWORD=... \\
        python benchmarks/bench_queries.py --base-url http://127.0.0.1:8052 --http --concurrency

Part 1 calls the same service functions the API routes call (``app.services.roads_query``), so each timing includes
the SQL and building the GeoJSON response object, but not HTTP. Each query runs ``--warmup`` times untimed and then
``--runs`` times timed; median, 95th percentile and maximum are reported, with the number of rows returned and the
plan PostgreSQL chose (EXPLAIN of the statement the service actually sent).

Part 2 (``--http``) signs in and times GET /api/v1/roads for a city-centre view (one page of up to 5,000 roads) and
for the whole-county view the dashboard loads (the first page, then the remaining pages four at a time, as
static/js/map.js does).

Part 3 (``--concurrency``) runs ``--clients`` simultaneous clients for ``--duration`` seconds, each repeatedly
requesting the city-centre view, and reports requests completed, errors and latency percentiles.

Read-only: nothing is written to the database. Uses only the standard library and the app's own dependencies.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import platform
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The parameters of each query in Table 5.7. The city-centre box is the example box of the API documentation.
CITY_CENTRE_BBOX = "36.78,-1.31,36.86,-1.26"
CBD_LAT, CBD_LON = -1.29, 36.82          # pipeline/load_vectors.py CBD_LONLAT
PAGE = 5000                               # static/js/map.js PAGE_LIMIT
COUNTY_ZOOM = 12                          # static/js/map.js DEFAULT_ZOOM


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100) of a non-empty list."""
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(-(-q / 100 * len(ordered) // 1)) - 1))
    return ordered[k]


def summarise(ms: list[float]) -> dict[str, float]:
    return {"n": len(ms), "median_ms": statistics.median(ms), "p95_ms": percentile(ms, 95), "max_ms": max(ms),
            "min_ms": min(ms)}


def machine() -> dict[str, str]:
    info = {"os": f"{platform.system()} {platform.release()}", "python": platform.python_version()}
    try:
        cpu = [line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
               if line.startswith("model name")]
        info["cpu"] = f"{cpu[0]} ({len(cpu)} logical CPUs)" if cpu else "unknown"
        mem = next(line for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal"))
        info["memory"] = f"{int(mem.split()[1]) / 1024 / 1024:.1f} GiB"
    except OSError:
        pass
    return info


# ---------------------------------------------------------------------------------------------- part 1: services
def service_queries(runs: int, warmup: int) -> tuple[list[dict], dict[str, str]]:
    from sqlalchemy import event, text

    from app.db import get_engine, session_scope
    from app.services import roads_query as rq
    from core.config_defaults import NAIROBI_BBOX

    engine = get_engine()
    last: dict[str, object] = {}

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ARG001
        last["statement"], last["parameters"] = statement, parameters

    county = rq.parse_bbox(",".join(str(v) for v in NAIROBI_BBOX))
    centre = rq.parse_bbox(CITY_CENTRE_BBOX)

    with session_scope() as db:
        one_road_id = db.execute(text(
            "SELECT road_id FROM road_vulnerability WHERE vulnerability_index IS NOT NULL ORDER BY road_id LIMIT 1")).scalar_one()
        versions = {
            "postgresql": db.execute(text("SHOW server_version")).scalar_one(),
            "postgis": db.execute(text("SELECT postgis_lib_version()")).scalar_one(),
            "roads_in_database": str(db.execute(text("SELECT count(*) FROM road_segments")).scalar_one()),
        }

    def rows_of(result) -> str:  # noqa: ANN001
        if hasattr(result, "features"):
            feats = result.features
            if feats and "segments" in (feats[0].properties or {}):
                return f"{len(feats)} names, {sum(f.properties['segments'] for f in feats)} segments"
            total = getattr(getattr(result, "pagination", None), "total", None)
            return f"{len(feats)} returned" + (f" of {total} matching" if total is not None else "")
        return "1 road"

    cases: list[tuple[str, str, Callable]] = [
        ("Map view of the city centre, page of 5,000", f"query_roads(bbox={CITY_CENTRE_BBOX}, limit=5000, zoom=15)",
         lambda db: rq.query_roads(db, centre, limit=PAGE, zoom=15)),
        ("All Severe roads in the county", "query_roads(county bbox, tier=Severe, limit=5000)",
         lambda db: rq.query_roads(db, county, tiers=["Severe"], limit=PAGE)),
        ("Primary and trunk roads with IVI 3.5 to 4.0", "query_roads(county bbox, highway=primary,trunk, 3.5<=IVI<=4.0)",
         lambda db: rq.query_roads(db, county, highways=["primary", "trunk"], min_ivi=3.5, max_ivi=4.0, limit=PAGE)),
        ("Roads within 500 m of the CBD", f"nearby_roads(lat={CBD_LAT}, lon={CBD_LON}, radius_m=500, limit=500)",
         lambda db: rq.nearby_roads(db, CBD_LAT, CBD_LON, 500.0, limit=500)),
        ('Name search "bypass"', 'search_roads(q="bypass", limit=10)',
         lambda db: rq.search_roads(db, "bypass", limit=10)),
        ("One road", f"get_road(road_id={one_road_id})",
         lambda db: rq.get_road(db, one_road_id)),
    ]
    results = []
    for name, call, fn in cases:
        timings: list[float] = []
        with session_scope() as db:
            for i in range(warmup + runs):
                t0 = time.perf_counter()
                out = fn(db)
                elapsed = (time.perf_counter() - t0) * 1000
                if i >= warmup:
                    timings.append(elapsed)
            statement, parameters = last["statement"], last["parameters"]
            with engine.connect() as conn:
                plan = [r[0] for r in conn.exec_driver_sql("EXPLAIN " + str(statement), parameters)]
        index_lines = [p.strip() for p in plan if "Index" in p or "Seq Scan" in p]
        results.append({"query": name, "call": call, "rows": rows_of(out), **summarise(timings),
                        "plan_scans": index_lines})
    return results, versions


# ---------------------------------------------------------------------------------------------- HTTP helpers
class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def clone(self) -> Api:
        other = Api(self.base)
        for cookie in self.jar:
            other.jar.set_cookie(cookie)
        return other

    def get(self, path: str, params: dict) -> tuple[int, bytes]:
        url = f"{self.base}/api/v1{path}?{urllib.parse.urlencode(params, doseq=True)}"
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        try:
            with self.opener.open(req, timeout=120) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def login(self, email: str, password: str) -> None:
        body = json.dumps({"email": email, "password": password}).encode()
        req = urllib.request.Request(f"{self.base}/api/v1/auth/login", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.opener.open(req, timeout=30) as resp:
            if resp.status != 200:
                raise SystemExit(f"sign-in failed: HTTP {resp.status}")


def _total(payload: bytes) -> int:
    import gzip

    try:
        data = json.loads(gzip.decompress(payload))
    except OSError:
        data = json.loads(payload)
    return int(data["pagination"]["total"])


# ---------------------------------------------------------------------------------------------- part 2: endpoint
def endpoint_timings(api: Api, runs: int, warmup: int) -> list[dict]:
    from core.config_defaults import NAIROBI_BBOX

    county = ",".join(str(v) for v in NAIROBI_BBOX)
    out = []

    # city centre: one page
    ms, matched, errors = [], None, 0
    for i in range(warmup + runs):
        t0 = time.perf_counter()
        status, body = api.get("/roads", {"bbox": CITY_CENTRE_BBOX, "zoom": 15, "limit": PAGE})
        elapsed = (time.perf_counter() - t0) * 1000
        if status != 200:
            errors += 1
            continue
        matched = _total(body)
        if i >= warmup:
            ms.append(elapsed)
    out.append({"request": f"GET /roads city centre (bbox={CITY_CENTRE_BBOX}, zoom=15, limit=5000)",
                "rows": f"{min(PAGE, matched or 0)} returned of {matched} matching", "errors": errors, **summarise(ms)})

    # whole county: first page, then the rest four at a time, as the dashboard does
    def county_view() -> tuple[float, int, int]:
        t0 = time.perf_counter()
        status, body = api.get("/roads", {"bbox": county, "zoom": COUNTY_ZOOM, "limit": PAGE, "offset": 0})
        if status != 200:
            return (time.perf_counter() - t0) * 1000, 0, 1
        total = _total(body)
        offsets = list(range(PAGE, total, PAGE))
        bad = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for st, _ in pool.map(lambda o: api.clone().get("/roads", {"bbox": county, "zoom": COUNTY_ZOOM,
                                                                        "limit": PAGE, "offset": o}), offsets):
                bad += st != 200
        return (time.perf_counter() - t0) * 1000, total, bad

    ms, total, errors = [], 0, 0
    for i in range(warmup + runs):
        elapsed, total, bad = county_view()
        errors += bad
        if i >= warmup:
            ms.append(elapsed)
    out.append({"request": f"GET /roads whole county (bbox={county}, zoom={COUNTY_ZOOM}), all pages of 5,000, 4 at a time",
                "rows": f"{total} roads in {-(-total // PAGE)} pages", "errors": errors, **summarise(ms)})
    return out


# ---------------------------------------------------------------------------------------------- part 3: concurrency
def concurrency(api: Api, clients: int, duration: float) -> dict:
    stop = time.monotonic() + duration
    lock = threading.Lock()
    latencies: list[float] = []
    errors: dict[str, int] = {}

    def worker() -> None:
        mine = api.clone()
        while time.monotonic() < stop:
            t0 = time.perf_counter()
            try:
                status, _ = mine.get("/roads", {"bbox": CITY_CENTRE_BBOX, "zoom": 15, "limit": PAGE})
                key = None if status == 200 else f"HTTP {status}"
            except Exception as exc:  # noqa: BLE001 - count every failure kind
                key = type(exc).__name__
            elapsed = (time.perf_counter() - t0) * 1000
            with lock:
                if key:
                    errors[key] = errors.get(key, 0) + 1
                else:
                    latencies.append(elapsed)

    started = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - started
    done = len(latencies)
    return {"clients": clients, "duration_s": round(wall, 1), "request": f"GET /roads city centre (limit=5000)",
            "completed": done, "errors": sum(errors.values()), "error_kinds": errors,
            "throughput_per_s": round(done / wall, 2), **(summarise(latencies) if latencies else {})}


# ---------------------------------------------------------------------------------------------- main
def fmt(v: float) -> str:
    return f"{v:,.1f}" if v >= 1 else f"{v:.2f}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--runs", type=int, default=30)
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--http", action="store_true", help="also time GET /roads over HTTP (needs a confirmed account)")
    p.add_argument("--concurrency", action="store_true", help="also run the concurrency test (implies --http)")
    p.add_argument("--clients", type=int, default=20)
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--json", help="also write all results to this JSON file")
    a = p.parse_args(argv)
    if a.runs < 30:
        p.error("--runs must be at least 30")

    report: dict = {"started_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "machine": machine(),
                    "runs": a.runs, "warmup": a.warmup}
    queries, versions = service_queries(a.runs, a.warmup)
    report["machine"].update(versions)
    report["queries"] = queries

    print(f"# AquaMap query benchmark ({report['started_utc']} UTC)\n")
    for k, v in report["machine"].items():
        print(f"- {k}: {v}")
    print(f"\n## Service-level queries ({a.runs} timed runs each, {a.warmup} warm-up)\n")
    print("| Query | Call | Rows | Median ms | p95 ms | Max ms |\n|---|---|---|---:|---:|---:|")
    for q in queries:
        print(f"| {q['query']} | `{q['call']}` | {q['rows']} | {fmt(q['median_ms'])} | {fmt(q['p95_ms'])} | {fmt(q['max_ms'])} |")
    print("\nPlans (scan nodes of EXPLAIN for the statement each call sent):\n")
    for q in queries:
        print(f"- {q['query']}: " + "; ".join(q["plan_scans"] or ["(no scan node)"]))

    if a.http or a.concurrency:
        email, password = os.environ.get("AQUAMAP_BENCH_EMAIL"), os.environ.get("AQUAMAP_BENCH_PASSWORD")
        if not email or not password:
            p.error("set AQUAMAP_BENCH_EMAIL and AQUAMAP_BENCH_PASSWORD for --http / --concurrency")
        api = Api(a.base_url)
        api.login(email, password)
        report["base_url"] = a.base_url
        report["endpoint"] = endpoint_timings(api, a.runs, a.warmup)
        print(f"\n## GET /roads over HTTP against {a.base_url} ({a.runs} timed runs each)\n")
        print("| Request | Rows | Errors | Median ms | p95 ms | Max ms |\n|---|---|---:|---:|---:|---:|")
        for e in report["endpoint"]:
            print(f"| {e['request']} | {e['rows']} | {e['errors']} | {fmt(e['median_ms'])} | {fmt(e['p95_ms'])} | {fmt(e['max_ms'])} |")
        if a.concurrency:
            c = concurrency(api, a.clients, a.duration)
            report["concurrency"] = c
            print(f"\n## Concurrency: {c['clients']} clients for {c['duration_s']} s, {c['request']}\n")
            print(f"- completed: {c['completed']}, errors: {c['errors']} {c['error_kinds'] or ''}")
            print(f"- throughput: {c['throughput_per_s']} requests/s")
            if c["completed"]:
                print(f"- latency ms: median {fmt(c['median_ms'])}, p95 {fmt(c['p95_ms'])}, max {fmt(c['max_ms'])}, "
                      f"min {fmt(c['min_ms'])}")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
