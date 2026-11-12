"""Application exceptions and their translation to JSON responses.

Routers and services raise these; the handlers registered in ``register_exception_handlers``
turn them into ``{"detail": "..."}`` bodies. Unexpected exceptions are logged server-side and
returned as a generic 500 with no internals.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("aquamap")


class AquaMapError(Exception):
    """Base class for errors that map to an HTTP status."""

    status_code: int = 500
    default_detail: str = "Internal server error."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.default_detail
        super().__init__(self.detail)


class NotImplementedYet(AquaMapError):
    """A route or service that exists in the API contract but is built in a later phase."""

    status_code = 501
    default_detail = "This feature is not implemented yet."

    def __init__(self, feature: str) -> None:
        super().__init__(f"Not implemented yet: {feature}.")


class AuthenticationError(AquaMapError):
    status_code = 401
    default_detail = "Authentication required."


class PermissionDenied(AquaMapError):
    status_code = 403
    default_detail = "You do not have permission to do that."


class NotFoundError(AquaMapError):
    status_code = 404
    default_detail = "Resource not found."


class ConflictError(AquaMapError):
    status_code = 409
    default_detail = "The request conflicts with existing data."


class InvalidRequestError(AquaMapError):
    status_code = 422
    default_detail = "The request is invalid."


class PayloadTooLargeError(AquaMapError):
    status_code = 413
    default_detail = "The requested result is too large; narrow the filters or the map area."


async def _handle_aquamap_error(_: Request, exc: AquaMapError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": AquaMapError.default_detail})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AquaMapError, _handle_aquamap_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected_error)
