"""The generated OpenAPI document describes the API as built (no database needed)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = {("get", "/api/v1/health"), ("post", "/api/v1/auth/register"), ("post", "/api/v1/auth/login"),
          ("post", "/api/v1/auth/logout"), ("post", "/api/v1/auth/verify-2fa"), ("post", "/api/v1/auth/resend-2fa")}
PAGINATED = ["/api/v1/roads", "/api/v1/roads/search", "/api/v1/roads/nearby"]


@pytest.fixture(scope="module")
def spec(client: TestClient) -> dict:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


def operations(spec: dict):
    for path, item in spec["paths"].items():
        for method, op in item.items():
            yield method, path, op


def ref_name(schema: dict) -> str:
    return schema.get("$ref", "").rsplit("/", 1)[-1]


def test_document_is_openapi_3_with_title_and_version(spec: dict) -> None:
    assert spec["openapi"].startswith("3.") and spec["info"]["title"] == "AquaMap API" and spec["info"]["version"]
    assert "not a flood forecast" in spec["info"]["description"]


def test_every_operation_has_a_summary_tag_and_typed_success_response(spec: dict) -> None:
    for method, path, op in operations(spec):
        assert op.get("summary"), (method, path)
        assert op.get("tags"), (method, path)
        success = op["responses"].get("200") or op["responses"].get("201")
        assert success, (method, path)
        if path.endswith("/roads/export"):
            continue  # a file download, not a JSON model
        schema = success["content"]["application/json"]["schema"]
        assert ref_name(schema) or schema.get("type") == "array", (method, path, schema)


def test_restricted_operations_declare_the_session_cookie_and_public_ones_do_not(spec: dict) -> None:
    scheme = spec["components"]["securitySchemes"]["SessionCookie"]
    assert scheme["type"] == "apiKey" and scheme["in"] == "cookie" and scheme["name"] == "aquamap_session"
    for method, path, op in operations(spec):
        secured = op.get("security") == [{"SessionCookie": []}]
        assert secured == ((method, path) not in PUBLIC), (method, path, op.get("security"))


def test_restricted_operations_document_401_and_all_errors_use_one_schema(spec: dict) -> None:
    for method, path, op in operations(spec):
        if (method, path) not in PUBLIC:
            assert "401" in op["responses"], (method, path)
        if path.startswith("/api/v1/admin"):
            assert "403" in op["responses"], (method, path)
        for code, response in op["responses"].items():
            if code.startswith(("4", "5")):
                assert ref_name(response["content"]["application/json"]["schema"]) == "ErrorResponse", (method, path, code)
    error = spec["components"]["schemas"]["ErrorResponse"]
    assert error["required"] == ["detail"] and set(error["properties"]) == {"detail", "errors"}


@pytest.mark.parametrize("path", PAGINATED)
def test_list_operations_are_paginated(spec: dict, path: str) -> None:
    op = spec["paths"][path]["get"]
    params = {p["name"]: p for p in op["parameters"]}
    assert {"limit", "offset"} <= set(params)
    assert params["limit"]["schema"]["minimum"] == 1 and params["limit"]["schema"]["maximum"] <= 5000
    assert params["offset"]["schema"]["minimum"] == 0
    collection = spec["components"]["schemas"][ref_name(op["responses"]["200"]["content"]["application/json"]["schema"])]
    assert "pagination" in collection["required"]
    assert set(spec["components"]["schemas"]["Pagination"]["properties"]) == \
        {"limit", "offset", "returned", "total", "has_more", "next_offset"}


def test_parameters_carry_their_limits(spec: dict) -> None:
    roads = {p["name"]: p["schema"] for p in spec["paths"]["/api/v1/roads"]["get"]["parameters"]}
    assert roads["bbox"] and "bbox" in [p["name"] for p in spec["paths"]["/api/v1/roads"]["get"]["parameters"] if p["required"]]
    for name in ("min_ivi", "max_ivi"):
        number = next(s for s in roads[name]["anyOf"] if s.get("type") == "number")
        assert (number["minimum"], number["maximum"]) == (1.0, 5.0)
    tiers = next(s for s in roads["tier"]["anyOf"] if s.get("type") == "array")
    assert tiers["items"]["enum"] == ["Low", "Medium", "High", "Severe"]
    near = {p["name"]: p["schema"] for p in spec["paths"]["/api/v1/roads/nearby"]["get"]["parameters"]}
    assert near["radius_m"]["exclusiveMinimum"] == 0 and near["radius_m"]["maximum"] == 2000
    assert (near["lat"]["minimum"], near["lat"]["maximum"]) == (-90, 90)
    search = {p["name"]: p["schema"] for p in spec["paths"]["/api/v1/roads/search"]["get"]["parameters"]}
    assert (search["q"]["minLength"], search["q"]["maxLength"]) == (2, 100)
    register = spec["components"]["schemas"]["RegisterRequest"]
    assert register["additionalProperties"] is False and register["properties"]["password"]["minLength"] == 8


def test_passwords_never_appear_in_response_schemas(spec: dict) -> None:
    for name in ("CurrentUser", "LoginResponse", "RegisterResponse"):
        text = json.dumps(spec["components"]["schemas"][name]).lower()
        assert "password" not in text and "hash" not in text, name


def test_interactive_docs_are_served(client: TestClient) -> None:
    page = client.get("/docs")
    assert page.status_code == 200 and "swagger" in page.text.lower()


def test_committed_openapi_file_matches_the_application() -> None:
    from app.openapi_export import DEFAULT_PATH, build

    assert DEFAULT_PATH.exists(), "run: python -m app.openapi_export"
    assert json.loads(DEFAULT_PATH.read_text()) == json.loads(build()), "run: python -m app.openapi_export"
