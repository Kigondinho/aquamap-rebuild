"""The API contract: paths exist, restricted endpoints are protected, errors share one JSON shape."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

EXPECTED_PATHS = {
    "/api/v1/health",
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/auth/verify-2fa",
    "/api/v1/auth/resend-2fa",
    "/api/v1/auth/logout",
    "/api/v1/auth/me",
    "/api/v1/roads",
    "/api/v1/roads/search",
    "/api/v1/roads/summary",
    "/api/v1/roads/export",
    "/api/v1/roads/nearby",
    "/api/v1/roads/{road_id}",
    "/api/v1/meta/config",
    "/api/v1/admin/layers",
    "/api/v1/admin/config",
    "/api/v1/admin/pipeline/status",
}

BBOX = "36.78,-1.31,36.86,-1.26"

# This client has NO database (see tests/conftest.py). Behaviour with data is tested in tests/db/.
PROTECTED = [
    ("GET", "/api/v1/auth/me", {}),
    ("GET", "/api/v1/roads", {"params": {"bbox": BBOX}}),
    ("GET", "/api/v1/roads/search", {"params": {"q": "mombasa"}}),
    ("GET", "/api/v1/roads/summary", {}),
    ("GET", "/api/v1/roads/export", {}),
    ("GET", "/api/v1/roads/nearby", {"params": {"lat": -1.286, "lon": 36.82}}),
    ("GET", "/api/v1/roads/1", {}),
    ("GET", "/api/v1/meta/config", {}),
    ("GET", "/api/v1/analysis/metadata", {}),
    ("GET", "/api/v1/admin/layers", {}),
    ("GET", "/api/v1/admin/config", {}),
    ("GET", "/api/v1/admin/pipeline/status", {}),
]
NOT_BUILT = [
    ("POST", "/api/v1/auth/verify-2fa", {"json": {"pending_token": "t", "otp_code": "123456"}}),
    ("POST", "/api/v1/auth/resend-2fa", {"json": {"pending_token": "t"}}),
]
NEEDS_DATABASE = [
    ("POST", "/api/v1/auth/register", {"json": {
        "full_name": "Test User", "email": "user@example.com",
        "password": "longenough1", "confirm_password": "longenough1"}}),
    ("POST", "/api/v1/auth/login", {"json": {"email": "user@example.com", "password": "x"}}),
]


def test_openapi_lists_every_contracted_path(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert EXPECTED_PATHS | {"/api/v1/analysis/metadata"} <= paths


@pytest.mark.parametrize(("method", "path", "kwargs"), PROTECTED)
def test_protected_endpoints_refuse_anonymous_requests(client: TestClient, method: str, path: str, kwargs: dict) -> None:
    """401 before anything else happens: no data, no database access, same JSON shape."""
    client.cookies.clear()
    response = client.request(method, path, **kwargs)
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Authentication required. Please sign in."}
    assert response.headers["www-authenticate"] == "Cookie"


@pytest.mark.parametrize(("method", "path", "kwargs"), PROTECTED)
def test_protected_endpoints_refuse_a_forged_session(client: TestClient, method: str, path: str, kwargs: dict) -> None:
    client.cookies.set("aquamap_session", "not.a.token")
    try:
        response = client.request(method, path, **kwargs)
    finally:
        client.cookies.clear()
    assert response.status_code == 401
    assert response.json()["detail"].startswith("Session is not valid")


@pytest.mark.parametrize(("method", "path", "kwargs"), NOT_BUILT)
def test_optional_second_factor_endpoints_answer_501(client: TestClient, method: str, path: str, kwargs: dict) -> None:
    response = client.request(method, path, **kwargs)
    assert response.status_code == 501, response.text
    assert response.json()["detail"].startswith("Not implemented yet")


@pytest.mark.parametrize(("method", "path", "kwargs"), NEEDS_DATABASE)
def test_database_outage_is_a_clean_503(client: TestClient, method: str, path: str, kwargs: dict) -> None:
    response = client.request(method, path, **kwargs)
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "The database is not available. Try again shortly."}


def test_logout_works_without_a_session(client: TestClient) -> None:
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 200 and response.json() == {"message": "Signed out."}


def test_invalid_body_is_rejected_with_422_in_the_common_shape(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"email": "not-an-email", "password": "x"})
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"detail", "errors"} and isinstance(body["detail"], str)
    assert body["errors"][0]["field"] == "email" and body["errors"][0]["message"]


def test_wrong_method_and_malformed_json_use_the_common_shape(client: TestClient) -> None:
    wrong = client.get("/api/v1/auth/login")
    assert wrong.status_code == 405 and wrong.json() == {"detail": "Method Not Allowed"}
    broken = client.post("/api/v1/auth/login", content="{not json", headers={"Content-Type": "application/json"})
    assert broken.status_code == 422 and isinstance(broken.json()["detail"], str)


def test_unknown_route_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404 and response.json() == {"detail": "Resource not found."}


@pytest.mark.parametrize("page", ["/", "/admin"])
def test_private_pages_redirect_anonymous_visitors_to_sign_in(client: TestClient, page: str) -> None:
    client.cookies.clear()
    response = client.get(page, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


@pytest.mark.parametrize("page", ["/", "/login", "/register", "/verify-2fa", "/admin"])
def test_pages_are_served(client: TestClient, page: str) -> None:
    response = client.get(page)  # private pages follow the redirect to the sign-in page
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>" in response.text


@pytest.mark.parametrize(
    "asset",
    [
        "/static/vendor/leaflet/leaflet.js",
        "/static/vendor/leaflet/leaflet.css",
        "/static/css/style.css",
        "/static/js/app.js",
        "/static/js/map.js",
    ],
)
def test_static_assets_are_served(client: TestClient, asset: str) -> None:
    assert client.get(asset).status_code == 200
