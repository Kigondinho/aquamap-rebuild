from __future__ import annotations

from fastapi.testclient import TestClient

from app.errors import AquaMapError, ConflictError, register_exception_handlers
from fastapi import FastAPI


def _app_with_failing_routes() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/conflict")
    def conflict() -> None:
        raise ConflictError("Email already registered.")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    return app


def test_known_errors_map_to_status_and_detail() -> None:
    client = TestClient(_app_with_failing_routes())
    response = client.get("/conflict")
    assert response.status_code == 409
    assert response.json() == {"detail": "Email already registered."}


def test_unexpected_errors_do_not_leak_internals() -> None:
    client = TestClient(_app_with_failing_routes(), raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": AquaMapError.default_detail}
    assert "secret internal detail" not in response.text
