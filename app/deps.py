"""Reusable FastAPI dependencies: database session, signed-in user, administrator."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import APIKeyCookie
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AuthenticationError, PermissionDenied
from app.schemas.auth import CurrentUser
from app.services import auth as auth_service
from app.services import sessions
from app.settings import get_settings

DbSession = Annotated[Session, Depends(get_db)]

# Declared as a security scheme so the OpenAPI document shows which routes need the session cookie.
# auto_error=False: a missing cookie is reported by us, in the API's own error shape.
session_cookie = APIKeyCookie(
    name=get_settings().session_cookie_name, scheme_name="SessionCookie", auto_error=False,
    description="HTTP-only session cookie set by POST /api/v1/auth/login.")


def get_current_user(db: DbSession, token: Annotated[str | None, Depends(session_cookie)] = None) -> CurrentUser:
    """Resolve the signed-in user from the session cookie.

    401 when the cookie is absent, expired or altered, or when the account no longer exists or is
    disabled. The role comes from the database, not from the token.
    """
    if not token:
        raise AuthenticationError("Authentication required. Please sign in.")
    user_id, _role_in_token = sessions.decode_session_token(token)
    return auth_service.get_active_user(db, user_id)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_admin(user: CurrentUserDep) -> CurrentUser:
    """Allow only users with the ``admin`` role (403 for everyone else who is signed in)."""
    if user.role != "admin":
        raise PermissionDenied("Administrator access required.")
    return user


AdminDep = Annotated[CurrentUser, Depends(require_admin)]
