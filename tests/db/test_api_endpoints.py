"""Every endpoint over HTTP with real sign-in: authentication, protection, pagination, GeoJSON, errors.

Runs against PostGIS inside a transaction that is rolled back. Roads are the SYNTHETIC network of
``test_road_queries`` (eight roads with known scores). Accounts are created per test with throw-away
passwords generated at run time.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from app.services import sessions
from app.services.rate_limit import login_limiter
from tests.db.conftest import NAIROBI_LAT, NAIROBI_LON
from tests.db.test_road_queries import db, world  # noqa: F401 - shared fixtures

pytestmark = pytest.mark.db

API = "/api/v1"
BBOX = "36.70,-1.40,36.95,-1.20"
COOKIE = "aquamap_session"


def new_password() -> str:
    return "Pw1-" + secrets.token_urlsafe(12)


@pytest.fixture(autouse=True)
def _fresh_limiter() -> Iterator[None]:
    login_limiter.reset()
    yield
    login_limiter.reset()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:  # noqa: F811
    from app.db import get_db
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client


def register(client: TestClient, email: str = "wanjiru@example.com", name: str = "Wanjiru Kamau",
             password: str | None = None) -> tuple[str, str]:
    password = password or new_password()
    response = client.post(f"{API}/auth/register", json={"full_name": name, "email": email, "password": password,
                                                         "confirm_password": password})
    assert response.status_code == 201, response.text
    return email, password


def sign_in(client: TestClient, email: str, password: str):
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


@pytest.fixture
def user(client: TestClient, world: dict) -> TestClient:  # noqa: F811
    """A client signed in as a normal user, with the synthetic road network loaded."""
    assert sign_in(client, *register(client)).status_code == 200
    return client


@pytest.fixture
def admin(client: TestClient, conn: Connection, world: dict) -> TestClient:  # noqa: F811
    email, password = register(client, "admin@example.com", "Site Admin")
    conn.execute(text("UPDATE users SET role = 'admin' WHERE email = :e"), {"e": email})
    assert sign_in(client, email, password).status_code == 200
    return client


# ================================================================ health
def test_health_is_public_and_reports_the_database(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.health.check_connection", lambda: True)
    body = client.get(f"{API}/health").json()
    assert body["status"] == "ok" and body["database"] is True and body["service"] == "AquaMap" and body["version"]
    monkeypatch.setattr("app.routers.health.check_connection", lambda: False)
    degraded = client.get(f"{API}/health")
    assert degraded.status_code == 200 and degraded.json()["status"] == "degraded"


# ================================================================ registration
def test_register_creates_a_user_account(client: TestClient, conn: Connection) -> None:
    password = new_password()
    response = client.post(f"{API}/auth/register", json={
        "full_name": "  Wanjiru   Kamau ", "email": "Wanjiru@Example.com", "password": password, "confirm_password": password})
    assert response.status_code == 201
    body = response.json()
    assert body["message"] == "Account created. Please sign in."
    assert body["user"]["email"] == "wanjiru@example.com" and body["user"]["full_name"] == "Wanjiru Kamau"
    assert body["user"]["role"] == "user" and set(body["user"]) == {"user_id", "email", "full_name", "role"}
    assert password not in response.text and COOKIE not in response.cookies  # registering does not sign in
    row = conn.execute(text("SELECT password_hash, role, is_active FROM users WHERE email = 'wanjiru@example.com'")).one()
    assert row.password_hash.startswith("$argon2id$") and password not in row.password_hash
    assert (row.role, row.is_active) == ("user", True)


def test_register_rejects_a_duplicate_email_whatever_its_capitals(client: TestClient) -> None:
    register(client, "dup@example.com")
    password = new_password()
    again = client.post(f"{API}/auth/register", json={"full_name": "Other Person", "email": "DUP@example.com",
                                                      "password": password, "confirm_password": password})
    assert again.status_code == 409 and again.json() == {"detail": "An account with this email already exists."}
    assert sign_in(client, "dup@example.com", password).status_code == 401  # the first account was not changed


@pytest.mark.parametrize(("change", "field"), [
    ({"confirm_password": "Different-1x"}, "body"),
    ({"password": "short1", "confirm_password": "short1"}, "password"),
    ({"password": "lettersonly", "confirm_password": "lettersonly"}, "password"),
    ({"email": "nope"}, "email"),
    ({"full_name": "A"}, "full_name"),
    ({"role": "admin"}, "role"),
])
def test_register_validation(client: TestClient, change: dict, field: str) -> None:
    payload = {"full_name": "Wanjiru Kamau", "email": "v@example.com", "password": "Valid-pass-1",
               "confirm_password": "Valid-pass-1"} | change
    response = client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert isinstance(body["detail"], str) and field in [e["field"] for e in body["errors"]]
    assert "Valid-pass-1" not in response.text  # the rejected value is not echoed back


def test_register_cannot_create_an_administrator(client: TestClient, conn: Connection) -> None:
    register(client, "plain@example.com")
    assert conn.execute(text("SELECT role FROM users WHERE email = 'plain@example.com'")).scalar_one() == "user"


# ================================================================ sign-in
def test_login_sets_an_http_only_session_cookie(client: TestClient) -> None:
    email, password = register(client)
    response = sign_in(client, email, password)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "authenticated" and body["requires_2fa"] is False and body["expires_in"] == 12 * 3600
    assert body["user"]["email"] == email and body["pending_token"] is None
    header = response.headers["set-cookie"].lower()
    assert header.startswith(f"{COOKIE}=") and "httponly" in header and "samesite=lax" in header and "path=/" in header
    assert "max-age=43200" in header
    token = response.cookies[COOKIE]
    assert token not in response.text and password not in response.text  # neither secret is in the body


def test_login_is_case_insensitive_on_email(client: TestClient) -> None:
    email, password = register(client, "mixed@example.com")
    assert sign_in(client, "MIXED@Example.COM", password).status_code == 200


def test_login_failures_give_one_generic_answer(client: TestClient, conn: Connection) -> None:
    email, password = register(client)
    wrong = sign_in(client, email, password + "x")
    unknown = sign_in(client, "nobody@example.com", password)
    conn.execute(text("UPDATE users SET is_active = FALSE WHERE email = :e"), {"e": email})
    disabled = sign_in(client, email, password)
    for response in (wrong, unknown, disabled):
        assert response.status_code == 401 and response.json() == {"detail": "Incorrect email or password."}
        assert "set-cookie" not in response.headers


def test_login_validation(client: TestClient) -> None:
    assert client.post(f"{API}/auth/login", json={"email": "x@example.com"}).status_code == 422
    assert client.post(f"{API}/auth/login", json={"email": "bad", "password": "x"}).status_code == 422
    assert client.post(f"{API}/auth/login", json={"email": "x@example.com", "password": ""}).status_code == 422
    assert client.post(f"{API}/auth/login", json={"email": "x@example.com", "password": "x", "admin": True}).status_code == 422


def test_login_is_blocked_after_five_failures_and_reports_when_to_retry(client: TestClient) -> None:
    email, password = register(client)
    for _ in range(5):
        assert sign_in(client, email, "Wrong-pass-1").status_code == 401
    blocked = sign_in(client, email, password)  # even the right password is refused while blocked
    assert blocked.status_code == 429 and "Too many failed sign-in attempts" in blocked.json()["detail"]
    assert int(blocked.headers["retry-after"]) > 0
    other, other_password = register(client, "other@example.com")
    assert sign_in(client, other, other_password).status_code == 200  # other accounts are not affected


def test_successful_login_clears_earlier_failures(client: TestClient) -> None:
    email, password = register(client)
    for _ in range(4):
        sign_in(client, email, "Wrong-pass-1")
    assert sign_in(client, email, password).status_code == 200
    for _ in range(4):
        assert sign_in(client, email, "Wrong-pass-1").status_code == 401


# ================================================================ current user and sign-out
def test_me_returns_the_signed_in_user(user: TestClient) -> None:
    body = user.get(f"{API}/auth/me").json()
    assert body["email"] == "wanjiru@example.com" and body["role"] == "user" and body["full_name"] == "Wanjiru Kamau"
    assert set(body) == {"user_id", "email", "full_name", "role"}


def test_me_requires_a_session(client: TestClient) -> None:
    response = client.get(f"{API}/auth/me")
    assert response.status_code == 401 and response.json() == {"detail": "Authentication required. Please sign in."}


def test_logout_ends_the_session(user: TestClient) -> None:
    response = user.post(f"{API}/auth/logout")
    assert response.status_code == 200 and response.json() == {"message": "Signed out."}
    assert f'{COOKIE}=""' in response.headers["set-cookie"] or "max-age=0" in response.headers["set-cookie"].lower()
    assert user.get(f"{API}/auth/me").status_code == 401


def test_altered_and_expired_sessions_are_refused(user: TestClient) -> None:
    good = user.cookies[COOKIE]
    user_id, role = sessions.decode_session_token(good)

    def with_cookie(value: str):
        user.cookies.clear()  # otherwise the client would send the original cookie as well
        return user.get(f"{API}/auth/me", headers={"Cookie": f"{COOKIE}={value}"})

    assert with_cookie(good).status_code == 200
    middle = len(good) - 20  # a character well inside the signature
    altered = good[:middle] + ("A" if good[middle] != "A" else "B") + good[middle + 1:]
    assert with_cookie(altered).status_code == 401
    payload_swapped = ".".join([good.split(".")[0], sessions.create_session_token(user_id, "admin").split(".")[1], good.split(".")[2]])
    assert with_cookie(payload_swapped).status_code == 401  # payload changed, signature kept
    expired = with_cookie(sessions.create_session_token(user_id, role, now=datetime.now(UTC) - timedelta(hours=13)))
    assert expired.status_code == 401 and "expired" in expired.json()["detail"]


def test_a_disabled_or_deleted_account_loses_access_at_once(user: TestClient, conn: Connection) -> None:
    assert user.get(f"{API}/roads/summary").status_code == 200
    conn.execute(text("UPDATE users SET is_active = FALSE WHERE email = 'wanjiru@example.com'"))
    assert user.get(f"{API}/roads/summary").status_code == 401
    conn.execute(text("DELETE FROM users WHERE email = 'wanjiru@example.com'"))
    assert user.get(f"{API}/auth/me").status_code == 401


def test_role_is_read_from_the_database_not_from_the_cookie(user: TestClient, conn: Connection) -> None:
    assert user.get(f"{API}/admin/layers").status_code == 403
    conn.execute(text("UPDATE users SET role = 'admin' WHERE email = 'wanjiru@example.com'"))
    assert user.get(f"{API}/admin/layers").status_code == 200       # promoted: same cookie now has access
    conn.execute(text("UPDATE users SET role = 'user' WHERE email = 'wanjiru@example.com'"))
    assert user.get(f"{API}/admin/layers").status_code == 403       # demoted: access is gone at once


# ================================================================ protection
PROTECTED = [f"{API}/auth/me", f"{API}/roads?bbox={BBOX}", f"{API}/roads/search?q=alpha", f"{API}/roads/summary",
             f"{API}/roads/nearby?lat=-1.29&lon=36.82", f"{API}/roads/1", f"{API}/meta/config",
             f"{API}/analysis/metadata", f"{API}/admin/layers", f"{API}/admin/config", f"{API}/admin/pipeline/status"]


@pytest.mark.parametrize("url", PROTECTED)
def test_anonymous_requests_get_401_and_no_data(client: TestClient, world: dict, url: str) -> None:  # noqa: F811
    response = client.get(url)
    assert response.status_code == 401 and set(response.json()) == {"detail"}
    assert "features" not in response.text and "Alpha" not in response.text


@pytest.mark.parametrize("url", [f"{API}/admin/layers", f"{API}/admin/config", f"{API}/admin/pipeline/status"])
def test_admin_routes_refuse_normal_users_and_serve_administrators(user: TestClient, conn: Connection, url: str) -> None:
    refused = user.get(url)
    assert refused.status_code == 403 and refused.json() == {"detail": "Administrator access required."}
    conn.execute(text("UPDATE users SET role = 'admin' WHERE email = 'wanjiru@example.com'"))
    assert user.get(url).status_code == 200


def test_admin_reads(admin: TestClient, world: dict) -> None:  # noqa: F811
    config = admin.get(f"{API}/admin/config").json()
    assert config["config_version"] == world["__config__"] and config["weights"]["elevation"] == 0.35
    assert config["tier_thresholds"] == {"Medium": 2.0, "High": 3.0, "Severe": 4.0}
    assert isinstance(admin.get(f"{API}/admin/layers").json(), list)
    status = admin.get(f"{API}/admin/pipeline/status").json()
    assert status["status"] in ("idle", "running", "succeeded", "failed")
    assert admin.put(f"{API}/admin/config", json=config).status_code == 501  # saving is not built


# ================================================================ roads: retrieval, filtering, pagination
def names(body: dict) -> list:
    return [f["properties"]["name"] for f in body["features"]]


def test_roads_returns_a_geojson_feature_collection_in_lon_lat(user: TestClient) -> None:
    body = user.get(f"{API}/roads", params={"bbox": BBOX}).json()
    assert body["type"] == "FeatureCollection" and len(body["features"]) == 8
    for feature in body["features"]:
        assert feature["type"] == "Feature" and feature["geometry"]["type"] == "LineString"
        assert set(feature["properties"]) == {"id", "name", "highway_type", "risk_category", "vulnerability_index"}
        for lon, lat in feature["geometry"]["coordinates"]:
            assert 36.6 < lon < 37.1 and -1.45 < lat < -1.15   # degrees, longitude first: EPSG:4326 as Leaflet's GeoJSON layer expects
    assert body["pagination"] == {"limit": 1000, "offset": 0, "returned": 8, "total": 8, "has_more": False, "next_offset": None}


def test_roads_filters(user: TestClient) -> None:
    get = lambda **p: names(user.get(f"{API}/roads", params={"bbox": BBOX, **p}).json())  # noqa: E731
    assert get(tier=["Severe"]) == ["Alpha Road", "Alpha Lane"]
    assert get(tier=["Low", "Medium"]) == ["Gamma 100% Way", "Delta_Road"]
    assert get(highway=["primary", "tertiary"]) == ["Alpha Road", "Delta_Road"]
    assert get(min_ivi=4.0) == ["Alpha Road", "Alpha Lane"]
    assert get(max_ivi=2.0) == ["Gamma 100% Way", "Delta_Road"]
    assert get(min_ivi=3.0, max_ivi=3.999) == ["Beta Street", "beta street"]
    assert get(tier=["Severe", "High"], highway=["residential"], min_ivi=3.5) == ["Alpha Lane", "Beta Street"]
    assert get(highway=["motorway"]) == []


def test_roads_pagination_walks_the_whole_result_without_gaps_or_repeats(user: TestClient) -> None:
    seen, offset, pages = [], 0, 0
    while offset is not None:
        body = user.get(f"{API}/roads", params={"bbox": BBOX, "limit": 3, "offset": offset}).json()
        page = body["pagination"]
        assert page["limit"] == 3 and page["offset"] == offset and page["total"] == 8
        assert page["returned"] == len(body["features"]) <= 3
        seen += [f["properties"]["id"] for f in body["features"]]
        offset, pages = page["next_offset"], pages + 1
    assert pages == 3 and len(seen) == 8 and len(set(seen)) == 8
    everything = [f["properties"]["id"] for f in user.get(f"{API}/roads", params={"bbox": BBOX}).json()["features"]]
    assert seen == everything  # same order as the unpaginated request


def test_roads_page_beyond_the_end_is_empty_but_still_reports_the_total(user: TestClient) -> None:
    body = user.get(f"{API}/roads", params={"bbox": BBOX, "limit": 3, "offset": 50}).json()
    assert body["features"] == []
    assert body["pagination"] == {"limit": 3, "offset": 50, "returned": 0, "total": 8, "has_more": False, "next_offset": None}


def test_roads_pagination_respects_filters(user: TestClient) -> None:
    body = user.get(f"{API}/roads", params={"bbox": BBOX, "tier": ["Severe", "High"], "limit": 3}).json()
    assert names(body) == ["Alpha Road", "Alpha Lane", "Beta Street"]
    assert body["pagination"]["total"] == 4 and body["pagination"]["next_offset"] == 3
    rest = user.get(f"{API}/roads", params={"bbox": BBOX, "tier": ["Severe", "High"], "limit": 3, "offset": 3}).json()
    assert names(rest) == ["beta street"] and rest["pagination"]["has_more"] is False


@pytest.mark.parametrize("params", [
    {}, {"bbox": "1,2,3"}, {"bbox": "a,b,c,d"}, {"bbox": "36.9,-1.2,36.7,-1.4"}, {"bbox": "10,50,11,51"},
    {"bbox": BBOX, "tier": "Critical"}, {"bbox": BBOX, "min_ivi": 0.9}, {"bbox": BBOX, "max_ivi": 5.1},
    {"bbox": BBOX, "min_ivi": 4, "max_ivi": 3}, {"bbox": BBOX, "min_ivi": "high"},
    {"bbox": BBOX, "limit": 0}, {"bbox": BBOX, "limit": 5001}, {"bbox": BBOX, "offset": -1},
    {"bbox": BBOX, "zoom": 30}, {"bbox": BBOX, "highway": "primary'; DROP TABLE users;--"},
    {"bbox": BBOX, "highway": "x" * 51},
])
def test_roads_rejects_invalid_parameters(user: TestClient, params: dict) -> None:
    response = user.get(f"{API}/roads", params=params)
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], str)
    assert user.get(f"{API}/roads", params={"bbox": BBOX}).json()["pagination"]["total"] == 8  # nothing was harmed


def test_roads_page_size_is_capped(user: TestClient) -> None:
    assert user.get(f"{API}/roads", params={"bbox": BBOX, "limit": 5000}).status_code == 200
    assert user.get(f"{API}/roads", params={"bbox": BBOX, "limit": 5001}).status_code == 422


# ================================================================ search
def test_search_finds_names_and_paginates(user: TestClient) -> None:
    body = user.get(f"{API}/roads/search", params={"q": "road"}).json()
    assert names(body) == ["Alpha Road", "Delta_Road", "Epsilon Road"] and body["pagination"]["total"] == 3
    first = user.get(f"{API}/roads/search", params={"q": "road", "limit": 2}).json()
    assert names(first) == ["Alpha Road", "Delta_Road"] and first["pagination"]["next_offset"] == 2
    second = user.get(f"{API}/roads/search", params={"q": "road", "limit": 2, "offset": 2}).json()
    assert names(second) == ["Epsilon Road"] and second["pagination"]["has_more"] is False
    props = user.get(f"{API}/roads/search", params={"q": "BETA"}).json()["features"][0]["properties"]
    assert props["segments"] == 2 and props["vulnerability_index"] == 3.999 and props["risk_category"] == "High"
    assert len(props["bbox"]) == 4
    assert user.get(f"{API}/roads/search", params={"q": "zzz"}).json()["pagination"]["total"] == 0


@pytest.mark.parametrize("params", [{}, {"q": "a"}, {"q": "x" * 101}, {"q": "road", "limit": 51}, {"q": "road", "limit": 0},
                                    {"q": "road", "offset": -1}])
def test_search_rejects_invalid_parameters(user: TestClient, params: dict) -> None:
    assert user.get(f"{API}/roads/search", params=params).status_code == 422


def test_search_text_cannot_inject_sql(user: TestClient, conn: Connection) -> None:
    response = user.get(f"{API}/roads/search", params={"q": "'; DELETE FROM road_segments; --"})
    assert response.status_code == 200 and response.json()["features"] == []
    assert conn.execute(text("SELECT count(*) FROM road_segments")).scalar_one() == 8


# ================================================================ nearby
def test_nearby_returns_roads_within_the_radius_nearest_first(user: TestClient) -> None:
    body = user.get(f"{API}/roads/nearby", params={"lat": NAIROBI_LAT, "lon": NAIROBI_LON, "radius_m": 500}).json()
    assert names(body) == [None, "Alpha Road", "Delta_Road", "Alpha Lane"]
    assert [round(f["properties"]["distance_m"], 1) for f in body["features"]] == [50.0, 100.0, 300.0, 499.9]
    assert body["pagination"]["total"] == 4 and body["type"] == "FeatureCollection"


def test_nearby_pagination_and_filters(user: TestClient) -> None:
    base = {"lat": NAIROBI_LAT, "lon": NAIROBI_LON, "radius_m": 2000}
    first = user.get(f"{API}/roads/nearby", params={**base, "limit": 3}).json()
    second = user.get(f"{API}/roads/nearby", params={**base, "limit": 3, "offset": 3}).json()
    third = user.get(f"{API}/roads/nearby", params={**base, "limit": 3, "offset": 6}).json()
    assert first["pagination"]["total"] == 7 and (first["pagination"]["next_offset"], second["pagination"]["next_offset"]) == (3, 6)
    distances = [f["properties"]["distance_m"] for page in (first, second, third) for f in page["features"]]
    assert len(distances) == 7 and distances == sorted(distances) and third["pagination"]["has_more"] is False
    severe = user.get(f"{API}/roads/nearby", params={**base, "tier": "Severe"}).json()
    assert names(severe) == ["Alpha Road", "Alpha Lane"]


@pytest.mark.parametrize("params", [
    {"lat": -1.29}, {"lon": 36.82}, {"lat": 95, "lon": 36.82}, {"lat": -1.29, "lon": 200},
    {"lat": -1.29, "lon": 36.82, "radius_m": 0}, {"lat": -1.29, "lon": 36.82, "radius_m": 2001},
    {"lat": -1.29, "lon": 36.82, "limit": 501}, {"lat": "south", "lon": 36.82},
])
def test_nearby_rejects_invalid_parameters(user: TestClient, params: dict) -> None:
    assert user.get(f"{API}/roads/nearby", params=params).status_code == 422


# ================================================================ detail
def test_road_detail(user: TestClient, world: dict) -> None:  # noqa: F811
    body = user.get(f"{API}/roads/{world['Alpha Road']}").json()
    assert body["type"] == "Feature" and body["geometry"]["type"] == "LineString"
    p = body["properties"]
    assert (p["name"], p["highway_type"], p["vulnerability_index"], p["risk_category"], p["scored"]) == \
        ("Alpha Road", "primary", 4.6, "Severe", True)
    assert p["factor_scores"] == {"elevation": 3.0, "slope": 4.0, "river": 4.0, "surface": 3.0}
    assert p["weights"] == {"elevation": 0.35, "slope": 0.2, "river": 0.3, "surface": 0.15}
    assert p["model_version"] == "test-model" and p["computed_at"] and p["config_version"] == world["__config__"]
    lon, lat = body["geometry"]["coordinates"][0]
    assert abs(lon - 36.8209) < 1e-3 and abs(lat - NAIROBI_LAT) < 1e-3


def test_road_detail_errors(user: TestClient, world: dict) -> None:  # noqa: F811
    missing = user.get(f"{API}/roads/2000000000")
    assert missing.status_code == 404 and missing.json() == {"detail": "Road not found."}
    for bad in ("0", "-4", "abc", "1.5"):
        assert user.get(f"{API}/roads/{bad}").status_code == 422
    unscored = user.get(f"{API}/roads/{world[None]}").json()["properties"]
    assert unscored["scored"] is False and unscored["vulnerability_index"] is None and unscored["factor_scores"] is None


# ================================================================ statistics
def test_statistics(user: TestClient) -> None:
    body = user.get(f"{API}/roads/summary").json()
    assert body["total_segments"] == 8 and body["total_length_km"] == pytest.approx(1.6)
    assert [(t["tier"], t["segments"]) for t in body["by_tier"]] == [("Low", 1), ("Medium", 1), ("High", 2), ("Severe", 2)]
    assert body["unscored_segments"] == 2 and body["unscored_length_km"] == pytest.approx(0.4)
    filtered = user.get(f"{API}/roads/summary", params={"highway": "residential", "min_ivi": 3.5}).json()
    assert filtered["total_segments"] == 2 and filtered["unscored_segments"] == 0
    assert user.get(f"{API}/roads/summary", params={"bbox": "nonsense"}).status_code == 422
    assert user.get(f"{API}/roads/summary", params={"min_ivi": 4.5, "max_ivi": 2}).status_code == 422


# ================================================================ metadata
def test_legend_metadata(user: TestClient, world: dict) -> None:  # noqa: F811
    body = user.get(f"{API}/meta/config").json()
    assert [(t["tier"], t["lower_bound"], t["upper_bound"]) for t in body["tiers"]] == \
        [("Low", 1.0, 2.0), ("Medium", 2.0, 3.0), ("High", 3.0, 4.0), ("Severe", 4.0, None)]
    assert body["config_version"] == world["__config__"] and "not a flood" in body["disclaimer"].lower()


def test_analysis_metadata_describes_the_stored_results(user: TestClient, world: dict) -> None:  # noqa: F811
    body = user.get(f"{API}/analysis/metadata").json()
    assert body["analysed"] is True and body["model_version"] == "test-model" and body["analysis_date"]
    assert body["config_version"] == world["__config__"]
    assert body["formula"].startswith("IVI = ") and (body["scale_min"], body["scale_max"]) == (1.0, 5.0)
    assert "not a flood" in body["interpretation"].lower() and "not been validated" in body["interpretation"]
    factors = {f["factor"]: f for f in body["factors"]}
    assert {k: v["weight"] for k, v in factors.items()} == {"elevation": 0.35, "slope": 0.2, "river": 0.3, "surface": 0.15}
    assert sum(f["weight"] for f in body["factors"]) == pytest.approx(1.0)
    assert factors["river"]["upper_bounds"] == [50.0, 150.0, 300.0, 500.0] and factors["river"]["scores"] == [5, 4, 3, 2, 1]
    assert factors["surface"]["upper_bounds"] is None and body["surface_scores"]["*"] == 3.0
    assert [t["tier"] for t in body["tiers"]] == ["Low", "Medium", "High", "Severe"] and "higher" in body["boundary_rule"]
    assert body["coverage"] == {"roads_total": 8, "roads_analysed": 7, "roads_with_index": 6, "roads_without_index": 1,
                                "roads_not_analysed": 1, "flags": {"no_dem": 1, "no_slope": 1}}
    assert (body["crs_storage"], body["crs_api"]) == ("EPSG:32737", "EPSG:4326")
    assert any("OpenStreetMap" in a for a in body["attribution"])


def test_analysis_metadata_before_any_analysis(user: TestClient, conn: Connection) -> None:
    conn.execute(text("DELETE FROM road_vulnerability"))
    body = user.get(f"{API}/analysis/metadata").json()
    assert body["analysed"] is False and body["model_version"] is None and body["analysis_date"] is None
    assert body["coverage"]["roads_analysed"] == 0 and body["coverage"]["roads_not_analysed"] == 8
    assert len(body["factors"]) == 4 and len(body["tiers"]) == 4  # the documented configuration is still described


# ================================================================ consistency
def test_every_error_has_the_same_json_shape(user: TestClient) -> None:
    responses = [
        user.get(f"{API}/roads"),                                  # 422 missing parameter
        user.get(f"{API}/roads", params={"bbox": "1,2,3"}),        # 422 from the service
        user.get(f"{API}/roads/2000000000"),                       # 404 from the service
        user.get(f"{API}/nothing-here"),                           # 404 unknown route
        user.post(f"{API}/roads/summary"),                         # 405
        user.get(f"{API}/admin/layers"),                           # 403
        user.get(f"{API}/roads/export"),                           # 501
    ]
    assert [r.status_code for r in responses] == [422, 422, 404, 404, 405, 403, 501]
    for response in responses:
        body = response.json()
        assert response.headers["content-type"].startswith("application/json")
        assert isinstance(body["detail"], str) and body["detail"] and set(body) <= {"detail", "errors"}
        assert "Traceback" not in response.text and "sqlalchemy" not in response.text.lower()


def test_unexpected_failures_do_not_leak_internals(db: Session, world: dict, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from app.db import get_db
    from app.main import create_app
    from app.services import roads_query

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app, raise_server_exceptions=False) as client:
        assert sign_in(client, *register(client, "boom@example.com")).status_code == 200
        monkeypatch.setattr(roads_query, "road_stats", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret internal detail")))
        response = client.get(f"{API}/roads/summary")
    assert response.status_code == 500 and response.json() == {"detail": "Internal server error."}
    assert "secret internal detail" not in response.text


# ================================================================ pages
def test_pages_follow_the_session(client: TestClient, conn: Connection) -> None:
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/login").status_code == 200 and client.get("/register").status_code == 200
    email, password = register(client)
    sign_in(client, email, password)
    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/"   # signed in, not an administrator
    conn.execute(text("UPDATE users SET role = 'admin' WHERE email = :e"), {"e": email})
    assert client.get("/admin", follow_redirects=False).status_code == 200
