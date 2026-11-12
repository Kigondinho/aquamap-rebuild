"""HTTP layer: parse requests, apply auth dependencies, call services, shape responses.

Routers contain no SQL and no scoring logic.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routers import admin, auth, health, meta, roads

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(roads.router)
api_router.include_router(meta.router)
api_router.include_router(admin.router)
