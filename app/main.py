"""Application factory. Run with: ``uvicorn app.main:app --reload``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
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
            "derived from elevation, slope, river proximity and surface; they are not a flood forecast."
        ),
        version=__version__,
        lifespan=lifespan,
        # Interactive docs are useful in development; hidden in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    register_exception_handlers(app)
    app.include_router(api_router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
