"""The frontend files are served, reference only files that exist, and carry no built-in data.

These checks need no browser and no database. Behaviour in the browser was verified by hand against the
running application (docs/phase11_frontend.md).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static"
PAGES = ["index.html", "login.html", "register.html", "verify-email.html", "reset-password.html"]
SCRIPTS = sorted(p.name for p in (STATIC / "js").glob("*.js"))


@pytest.mark.parametrize("url", ["/login", "/register", "/verify-email", "/reset-password"])
def test_public_pages_are_served(client: TestClient, url: str) -> None:
    client.cookies.clear()
    response = client.get(url, follow_redirects=False)
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    assert "<title>" in response.text and 'name="viewport"' in response.text


def test_dashboard_needs_a_session(client: TestClient) -> None:
    client.cookies.clear()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


@pytest.mark.parametrize("name", SCRIPTS + ["../css/style.css"])
def test_assets_are_served(client: TestClient, name: str) -> None:
    assert client.get(f"/static/js/{name}").status_code == 200


@pytest.mark.parametrize("page", PAGES + ["admin.html", "verify-2fa.html"])
def test_every_page_declares_the_icon(page: str) -> None:
    assert '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">' in (STATIC / page).read_text(encoding="utf-8")


def test_favicon_is_served_where_browsers_look_for_it(client: TestClient) -> None:
    for url in ("/favicon.ico", "/static/favicon.svg"):
        response = client.get(url)
        assert response.status_code == 200 and response.headers["content-type"].startswith("image/svg+xml"), url


@pytest.mark.parametrize("page", PAGES)
def test_pages_reference_only_files_that_exist(page: str) -> None:
    html = (STATIC / page).read_text(encoding="utf-8")
    references = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
    assert references, page
    for reference in references:
        assert (ROOT / reference.lstrip("/")).is_file(), f"{page} -> {reference}"
    assert "http://" not in html  # no mixed content; nothing loaded from another site by the page itself


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_imports_resolve(script: str) -> None:
    source = (STATIC / "js" / script).read_text(encoding="utf-8")
    for target in re.findall(r'from "(\./[^"]+)"', source):
        assert (STATIC / "js" / target).is_file(), f"{script} imports {target}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_are_valid_javascript_modules(script: str) -> None:
    source = (STATIC / "js" / script).read_text(encoding="utf-8")
    result = subprocess.run(["node", "--input-type=module", "--check"], input=source, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_dashboard_contains_no_built_in_statistics_or_roads() -> None:
    """Every figure and road name on the dashboard must come from the API at run time."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    stats = re.search(r'<section class="stats".*?</section>', html, re.S).group(0)
    assert not re.search(r"\d", re.sub(r"<!--.*?-->", "", stats, flags=re.S)), "statistics must not contain numbers in the page"
    assert "km" not in stats
    for script in SCRIPTS:
        source = (STATIC / "js" / script).read_text(encoding="utf-8")
        assert "FeatureCollection" not in source, f"{script}: no embedded GeoJSON"
        assert not re.search(r"\b\d{1,3}(,\d{3})+\b", source), f"{script}: no written-out totals"
        assert not re.search(r"\[\s*-?1\.\d{3,}\s*,\s*36\.\d{3,}\s*\]\s*,\s*\[", source), f"{script}: no embedded coordinates"


def test_every_dashboard_feature_calls_its_api_endpoint() -> None:
    code = "\n".join((STATIC / "js" / s).read_text(encoding="utf-8") for s in SCRIPTS)
    for endpoint in ('"/roads"', '"/roads/search"', '"/roads/nearby"', '"/roads/summary"', "`/roads/${roadId}`",
                     '"/meta/config"', '"/analysis/metadata"', '"/auth/me"', '"/auth/login"', '"/auth/register"',
                     '"/auth/logout"', '"/auth/verify-email"', '"/auth/password/forgot"', '"/auth/password/reset"'):
        assert endpoint in code, endpoint


def test_category_thresholds_are_not_duplicated_in_the_frontend() -> None:
    """The frontend styles categories; it never decides which category a score belongs to."""
    code = "\n".join((STATIC / "js" / s).read_text(encoding="utf-8") for s in SCRIPTS)
    assert not re.search(r"(>=|<)\s*[234]\.0\b", code) and "tier_thresholds" not in code


def test_api_text_is_never_inserted_as_html() -> None:
    code = "\n".join((STATIC / "js" / s).read_text(encoding="utf-8") for s in SCRIPTS)
    assert "innerHTML" not in code and "insertAdjacentHTML" not in code and "document.write" not in code
