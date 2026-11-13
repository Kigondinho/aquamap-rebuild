"""Serves the static HTML pages.

The dashboard needs a valid session and the admin page an administrator; otherwise the browser is
redirected (to the sign-in page, or to the dashboard for a signed-in non-admin). The sign-in and
registration pages are public. The data itself is protected separately by the API routes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.deps import DbSession
from app.errors import AuthenticationError
from app.schemas.auth import CurrentUser
from app.services import auth as auth_service
from app.services import sessions
from app.settings import get_settings

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

router = APIRouter(include_in_schema=False)


def _page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, media_type="text/html")


def optional_user(db: DbSession,
                  token: Annotated[str | None, Cookie(alias=get_settings().session_cookie_name)] = None) -> CurrentUser | None:
    """The signed-in user, or None (no cookie, expired, altered, account gone). Never raises 401."""
    if not token:
        return None
    try:
        user_id, _ = sessions.decode_session_token(token)
        return auth_service.get_active_user(db, user_id)
    except AuthenticationError:
        return None


OptionalUser = Annotated[CurrentUser | None, Depends(optional_user)]


@router.get("/")
def dashboard(user: OptionalUser) -> Response:
    return _page("index.html") if user else RedirectResponse("/login", status_code=303)


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
def admin_page(user: OptionalUser) -> Response:
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return _page("admin.html") if user.role == "admin" else RedirectResponse("/", status_code=303)
