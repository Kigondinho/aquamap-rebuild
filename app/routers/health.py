from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.db import check_connection
from app.settings import get_settings

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    database: bool


@router.get("/health", response_model=HealthResponse, summary="Liveness and database check")
def health() -> HealthResponse:
    """Always answers 200 so the process can be probed; ``degraded`` means the database is unreachable."""
    database_ok = check_connection()
    return HealthResponse(
        status="ok" if database_ok else "degraded",
        service=get_settings().app_name,
        version=__version__,
        database=database_ok,
    )
