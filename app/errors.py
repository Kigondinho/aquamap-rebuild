"""Application exceptions and their translation to JSON responses.

Routers and services raise these; the handlers registered in ``register_exception_handlers``
turn them into JSON. Every error response, whatever its cause, has the same shape:

    {"detail": "One sentence saying what went wrong."}

Validation failures (422) add ``"errors": [{"field": "...", "message": "..."}]`` with one entry per
rejected parameter. Unexpected exceptions are logged server-side and returned as a generic 500 with
no internals; a database that cannot be reached is a 503.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

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


class TooManyRequestsError(AquaMapError):
    status_code = 429
    default_detail = "Too many attempts. Try again later."

    def __init__(self, detail: str | None = None, retry_after: int | None = None) -> None:
        super().__init__(detail)
        self.retry_after = retry_after


class ServiceUnavailableError(AquaMapError):
    status_code = 503
    default_detail = "The database is not available. Try again shortly."


async def _handle_aquamap_error(_: Request, exc: AquaMapError) -> JSONResponse:
    headers = {}
    if isinstance(exc, TooManyRequestsError) and exc.retry_after:
        headers["Retry-After"] = str(exc.retry_after)
    if isinstance(exc, AuthenticationError):
        headers["WWW-Authenticate"] = "Cookie"
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=headers or None)


def _field_name(location: tuple) -> str:
    """``("query", "bbox")`` -> ``bbox``; ``("body", "email")`` -> ``email``; ``("body",)`` -> ``body``."""
    parts = [str(p) for p in location if p not in ("query", "path", "body", "header", "cookie")]
    return ".".join(parts) or str(location[0] if location else "request")


async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for item in exc.errors():
        message = str(item.get("msg", "Invalid value."))
        message = message.removeprefix("Value error, ")
        errors.append({"field": _field_name(tuple(item.get("loc", ()))), "message": message[:1].upper() + message[1:]})
    fields = ", ".join(dict.fromkeys(e["field"] for e in errors))
    return JSONResponse(status_code=422, content={"detail": f"Invalid request: check {fields}.", "errors": errors})


async def _handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Framework errors (unknown route, wrong method) in the same shape as our own."""
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    if exc.status_code == 404 and detail == "Not Found":
        detail = "Resource not found."
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)


async def _handle_database_unavailable(request: Request, exc: DBAPIError) -> JSONResponse:
    if isinstance(exc, (OperationalError, InterfaceError)):
        logger.error("Database unavailable on %s %s (%s)", request.method, request.url.path, type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": ServiceUnavailableError.default_detail})
    return await _handle_unexpected_error(request, exc)


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": AquaMapError.default_detail})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AquaMapError, _handle_aquamap_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(DBAPIError, _handle_database_unavailable)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected_error)
