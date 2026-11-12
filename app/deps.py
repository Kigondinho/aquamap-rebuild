"""Reusable FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import NotImplementedYet, PermissionDenied
from app.schemas.auth import CurrentUser

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(request: Request) -> CurrentUser:
    """Resolve the signed-in user from the session cookie.

    Phase 2 scaffold. The real implementation (later phase) reads the HTTP-only cookie, verifies the
    signed token and loads the active user; it raises ``AuthenticationError`` when absent or invalid.
    """
    raise NotImplementedYet("session authentication")


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_admin(user: CurrentUserDep) -> CurrentUser:
    """Allow only users with the ``admin`` role."""
    if user.role != "admin":
        raise PermissionDenied("Administrator access required.")
    return user


AdminDep = Annotated[CurrentUser, Depends(require_admin)]
