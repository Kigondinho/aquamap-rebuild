"""Application factory. Run with: ``uvicorn app.main:app --reload``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.db import dispose_engine
from app.errors import register_exception_handlers
from app.routers import api_router, pages
from app.routers.pages import STATIC_DIR
from app.settings import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)

    app = FastAPI(
        title="AquaMap API",
        description=(
            "Relative flood vulnerability of Nairobi County roads. Scores are a static planning aid "
            "derived from elevation, slope, river proximity and surface; they are not a flood forecast, "
            "a flood probability or a measured flood risk.\n\n"
            "**Access.** Sign in with `POST /api/v1/auth/login`; the session is an HTTP-only cookie. "
            "All road, statistics and metadata routes need a session; `/admin` routes need an administrator.\n\n"
            "**Geometry.** GeoJSON in EPSG:4326 (longitude, latitude).\n\n"
            "**Lists** are paginated with `limit` and `offset` and report `pagination.total`.\n\n"
            "**Errors** always have the body `{\"detail\": \"...\"}`; validation errors (422) add an `errors` list."
        ),
        openapi_tags=[
            {"name": "Health", "description": "Liveness and database check (public)."},
            {"name": "Authentication", "description": "Registration, sign-in, sign-out, current user."},
            {"name": "Roads", "description": "Road retrieval, filtering, search, nearby roads, detail and statistics."},
            {"name": "Analysis", "description": "Metadata of the stored vulnerability analysis."},
            {"name": "Meta", "description": "Category ranges and weights for the map legend."},
            {"name": "Admin", "description": "Administrator-only reads."},
        ],
        version=__version__,
        lifespan=lifespan,
        # Interactive docs are useful in development; hidden in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    register_exception_handlers(app)
    # GeoJSON compresses well (about 5x): large map pages cost far less to transfer.
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.include_router(api_router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
