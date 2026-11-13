"""Signed session tokens (JWT, HS256) carried in an HTTP-only cookie.

The token only identifies the user and when it expires. The account's role and active state are read
from the database on every request, so disabling a user or changing a role takes effect immediately.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import Response

from app.errors import AuthenticationError, NotImplementedYet
from app.settings import get_settings

ALGORITHM = "HS256"
TOKEN_TYPE = "session"


def create_session_token(user_id: UUID, role: str, *, now: datetime | None = None) -> str:
    """Token stored in the HTTP-only session cookie."""
    settings = get_settings()
    issued = now or datetime.now(UTC)
    payload = {"sub": str(user_id), "role": role, "typ": TOKEN_TYPE, "iat": issued,
               "exp": issued + timedelta(hours=settings.session_ttl_hours)}
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=ALGORITHM)


def decode_session_token(token: str) -> tuple[UUID, str]:
    """Return ``(user_id, role)`` or raise ``AuthenticationError`` (expired, altered, wrong kind, malformed)."""
    try:
        payload = jwt.decode(token, get_settings().secret_key.get_secret_value(), algorithms=[ALGORITHM],
                             options={"require": ["sub", "exp", "iat", "typ"]})
        if payload["typ"] != TOKEN_TYPE:
            raise AuthenticationError("Session is not valid. Please sign in again.")
        return UUID(payload["sub"]), str(payload.get("role", "user"))
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Session expired. Please sign in again.") from exc
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise AuthenticationError("Session is not valid. Please sign in again.") from exc


def set_session_cookie(response: Response, token: str) -> None:
    """HTTP-only (not readable by scripts), SameSite=Lax (not sent on cross-site POSTs), Secure in production."""
    settings = get_settings()
    response.set_cookie(settings.session_cookie_name, token, max_age=settings.session_ttl_hours * 3600,
                        httponly=True, samesite="lax", secure=settings.is_production, path="/")


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/", httponly=True, samesite="lax",
                           secure=settings.is_production)


def create_pending_token(user_id: UUID, otp_id: int) -> str:
    """Short-lived token that links a login attempt to its one-time code (optional 2FA module, not built)."""
    raise NotImplementedYet("pending 2FA token creation")


def decode_pending_token(token: str) -> tuple[UUID, int]:
    """Return ``(user_id, otp_id)`` or raise ``AuthenticationError`` (optional 2FA module, not built)."""
    raise NotImplementedYet("pending 2FA token verification")
