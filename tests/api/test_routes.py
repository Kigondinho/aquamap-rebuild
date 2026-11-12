"""The API contract exists and unimplemented endpoints answer 501 with a JSON body."""

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

NOT_IMPLEMENTED = [
    ("POST", "/api/v1/auth/register", {"json": {
        "full_name": "Test User", "email": "user@example.com",
        "password": "longenough1", "confirm_password": "longenough1"}}),
    ("POST", "/api/v1/auth/login", {"json": {"email": "user@example.com", "password": "x"}}),
    ("POST", "/api/v1/auth/verify-2fa", {"json": {"pending_token": "t", "otp_code": "123456"}}),
    ("POST", "/api/v1/auth/resend-2fa", {"json": {"pending_token": "t"}}),
    ("GET", "/api/v1/auth/me", {}),
    ("GET", "/api/v1/roads", {"params": {"bbox": BBOX}}),
    ("GET", "/api/v1/roads/search", {"params": {"q": "mombasa"}}),
    ("GET", "/api/v1/roads/summary", {}),
    ("GET", "/api/v1/roads/export", {}),
    ("GET", "/api/v1/roads/nearby", {"params": {"lat": -1.286, "lon": 36.82}}),
    ("GET", "/api/v1/roads/1", {}),
    ("GET", "/api/v1/meta/config", {}),
    ("GET", "/api/v1/admin/layers", {}),
    ("GET", "/api/v1/admin/config", {}),
    ("GET", "/api/v1/admin/pipeline/status", {}),
]


def test_openapi_lists_every_contracted_path(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert EXPECTED_PATHS <= paths


@pytest.mark.parametrize(("method", "path", "kwargs"), NOT_IMPLEMENTED)
def test_unimplemented_endpoints_return_501_json(
    client: TestClient, method: str, path: str, kwargs: dict
) -> None:
    response = client.request(method, path, **kwargs)
    assert response.status_code == 501, response.text
    assert response.json()["detail"].startswith("Not implemented yet")


def test_logout_is_a_harmless_placeholder(client: TestClient) -> None:
    assert client.post("/api/v1/auth/logout").status_code == 200


def test_invalid_body_is_rejected_with_422(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"email": "not-an-email", "password": "x"})
    assert response.status_code == 422


def test_unknown_route_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/does-not-exist").status_code == 404


@pytest.mark.parametrize("page", ["/", "/login", "/register", "/verify-2fa", "/admin"])
def test_pages_are_served(client: TestClient, page: str) -> None:
    response = client.get(page)
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
