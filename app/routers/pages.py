"""Serves the static HTML pages.

Phase 2 serves the pages without checking the session. The redirect-to-login guard for ``/``
and ``/admin`` is added together with authentication in a later phase; until then the data API
endpoints are the protected surface (they return 501 until implemented).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

router = APIRouter(include_in_schema=False)


def _page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, media_type="text/html")


@router.get("/")
def dashboard() -> FileResponse:
    return _page("index.html")


@router.get("/login")
def login_page() -> FileResponse:
    return _page("login.html")


@router.get("/register")
def register_page() -> FileResponse:
    return _page("register.html")


@router.get("/verify-2fa")
def verify_2fa_page() -> FileResponse:
    return _page("verify-2fa.html")


@router.get("/admin")
def admin_page() -> FileResponse:
    return _page("admin.html")
