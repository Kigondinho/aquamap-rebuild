from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_reports_degraded_when_database_unreachable(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] is False
    assert body["service"] == "AquaMap"
    assert body["version"]


def test_docs_available_outside_production(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
