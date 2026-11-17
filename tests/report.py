"""End-of-run report: passed, failed and unverified tests, by area of the system.

Printed after every run. ``pytest --report=FILE`` also writes it as Markdown (write it as one word with
``=``: see the note in the root ``conftest.py``).

* passed      the test ran and every check held
* failed      the test ran and a check did not hold, or the test could not be set up (an error)
* unverified  the test did not run (skipped), so it proves nothing either way; the reason is listed
"""

from __future__ import annotations

import platform
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

# First match wins, so the more specific paths come first.
AREAS: list[tuple[str, tuple[str, ...]]] = [
    ("Unit: IVI, normalisation, weights, classification, missing data",
     ("tests/unit/test_ivi.py", "tests/unit/test_config_defaults.py", "tests/unit/test_roads_query_parsing.py")),
    ("Unit: geospatial calculations", ("tests/pipeline/test_compute_metrics.py", "tests/pipeline/test_prepare_dem.py",
                                       "tests/pipeline/test_load_prepare.py")),
    ("Unit: data acquisition", ("tests/pipeline/",)),
    ("Authentication", ("tests/unit/test_auth_units.py", "tests/unit/test_email_and_otp_units.py",
                        "tests/db/test_auth_flows.py")),
    ("Unit: settings, database tooling, test-database guard", ("tests/unit/",)),
    ("Integration: database, schema, constraints", ("tests/db/test_connection.py", "tests/db/test_schema",
                                                    "tests/db/test_constraints.py")),
    ("Integration: ingestion and full pipeline", ("tests/db/test_ingestion.py", "tests/db/test_pipeline_end_to_end.py")),
    ("Integration: spatial queries, filtering, road detail", ("tests/db/test_spatial.py", "tests/db/test_road_queries.py",
                                                              "tests/db/test_classification.py", "tests/db/test_scoring.py")),
    ("Integration: API responses", ("tests/db/test_api_endpoints.py", "tests/api/test_routes.py", "tests/api/test_openapi.py",
                                    "tests/api/test_health.py", "tests/api/test_errors.py")),
    ("Frontend: in a real browser", ("tests/e2e/",)),
    ("Frontend: files and API wiring", ("tests/api/test_frontend.py",)),
    ("Project: structure and secrets", ("tests/test_",)),
]
OTHER = "Other"
STATUSES = ("passed", "failed", "unverified")


def area_of(nodeid: str) -> str:
    path = nodeid.split("::", 1)[0]
    for label, prefixes in AREAS:
        if path.startswith(prefixes):
            return label
    return OTHER


def _skip_reason(report: pytest.TestReport) -> str:
    longrepr = report.longrepr
    reason = longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) == 3 else str(longrepr)
    return reason.removeprefix("Skipped: ").strip()


def collect(terminalreporter: pytest.TerminalReporter) -> dict:
    """One status per test (a failed setup, call or teardown makes the test failed)."""
    status: dict[str, str] = {}
    reasons: dict[str, str] = {}
    failures: dict[str, str] = {}
    for key in ("passed", "skipped", "failed", "error"):
        for report in terminalreporter.stats.get(key, []):
            nodeid = getattr(report, "nodeid", None)
            if not nodeid:
                continue
            if key in ("failed", "error"):
                status[nodeid] = "failed"
                crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
                message = (getattr(crash, "message", "") or report.longreprtext or key).strip().splitlines()[0]
                stage = "" if report.when == "call" else f"[{report.when}] "
                failures.setdefault(nodeid, f"{stage}{message[:300]}")
            elif key == "skipped" and status.get(nodeid) != "failed":
                status[nodeid] = "unverified"
                reasons[nodeid] = _skip_reason(report)
            elif key == "passed" and nodeid not in status:
                status[nodeid] = "passed"
    by_area: dict[str, Counter] = defaultdict(Counter)
    for nodeid, state in status.items():
        by_area[area_of(nodeid)][state] += 1
    totals = Counter(status.values())
    unverified_reasons = Counter(reasons[n] for n, s in status.items() if s == "unverified")
    return {"by_area": by_area, "totals": totals, "failures": {n: failures[n] for n in sorted(failures) if status.get(n) == "failed"},
            "unverified_reasons": unverified_reasons}


def _rows(data: dict) -> list[tuple[str, int, int, int]]:
    order = [label for label, _ in AREAS] + [OTHER]
    return [(label, data["by_area"][label]["passed"], data["by_area"][label]["failed"], data["by_area"][label]["unverified"])
            for label in order if sum(data["by_area"][label].values())]


def as_text(data: dict) -> str:
    rows = _rows(data)
    width = max([len(r[0]) for r in rows] + [5])
    lines = [f"{'Area':<{width}}  {'passed':>7}  {'failed':>7}  {'unverified':>10}"]
    lines += [f"{label:<{width}}  {p:>7}  {f:>7}  {u:>10}" for label, p, f, u in rows]
    t = data["totals"]
    lines.append(f"{'TOTAL':<{width}}  {t['passed']:>7}  {t['failed']:>7}  {t['unverified']:>10}")
    if data["failures"]:
        lines += ["", "Failed:"] + [f"  {nodeid}\n      {message}" for nodeid, message in data["failures"].items()]
    if data["unverified_reasons"]:
        lines += ["", "Unverified (did not run, so not verified):"]
        lines += [f"  {count:>4} x {reason}" for reason, count in data["unverified_reasons"].most_common()]
    return "\n".join(lines)


def as_markdown(data: dict, config: pytest.Config) -> str:
    t = data["totals"]
    lines = [
        "# AquaMap test report", "",
        f"Run: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · Python {platform.python_version()} · "
        f"`pytest {' '.join(config.invocation_params.args)}`".rstrip(), "",
        f"**{t['passed']} passed · {t['failed']} failed · {t['unverified']} unverified** "
        f"(of {sum(t.values())} tests).", "",
        "| Area | Passed | Failed | Unverified |", "|---|---:|---:|---:|",
    ]
    lines += [f"| {label} | {p} | {f} | {u} |" for label, p, f, u in _rows(data)]
    lines.append(f"| **Total** | **{t['passed']}** | **{t['failed']}** | **{t['unverified']}** |")
    lines += ["", "*Passed*: ran and every check held. *Failed*: ran and a check did not hold, or could not be set up. "
              "*Unverified*: did not run (skipped), so it proves nothing either way."]
    if data["failures"]:
        lines += ["", "## Failed", ""] + [f"- `{nodeid}`: {message}" for nodeid, message in data["failures"].items()]
    lines += ["", "## Unverified", ""]
    if data["unverified_reasons"]:
        lines += [f"- {count} × {reason}" for reason, count in data["unverified_reasons"].most_common()]
    else:
        lines.append("None: every collected test ran.")
    return "\n".join(lines) + "\n"


def add_option(parser: pytest.Parser) -> None:
    parser.addoption("--report", metavar="FILE", default=None,
                     help="also write the passed / failed / unverified report to FILE as Markdown; "
                          "write it as --report=FILE (one word)")


def terminal_summary(terminalreporter: pytest.TerminalReporter, config: pytest.Config) -> None:
    data = collect(terminalreporter)
    if not sum(data["totals"].values()):
        return
    terminalreporter.write_sep("=", "AquaMap test report")
    terminalreporter.write_line(as_text(data))
    target = config.getoption("--report")
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(as_markdown(data, config), encoding="utf-8")
        terminalreporter.write_line(f"\nreport written to {path}")
    unverified = data["totals"]["unverified"]
    if config.getoption("--require-all", default=False) and unverified:
        terminalreporter.write_line(f"\n--require-all: {unverified} tests did not run, so this run counts as FAILED.",
                                    red=True, bold=True)
    if sys.stdout.isatty():
        terminalreporter.write_line("")
