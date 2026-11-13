from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Tier = Literal["Low", "Medium", "High", "Severe"]


class FieldError(BaseModel):
    field: str
    message: str


class ErrorResponse(BaseModel):
    """Body of every error response. ``errors`` is present only for validation failures (422)."""

    detail: str
    errors: list[FieldError] | None = None


class MessageResponse(BaseModel):
    message: str


class Pagination(BaseModel):
    """Where this page sits in the full result. Ask for the next page with ``offset = next_offset``."""

    limit: int = Field(description="Largest number of items this request could return")
    offset: int = Field(description="Number of items skipped before this page")
    returned: int = Field(description="Number of items in this page")
    total: int = Field(description="Number of items matching the request in all pages")
    has_more: bool
    next_offset: int | None = Field(default=None, description="Offset of the next page; null on the last page")


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` entries for the given status codes, all using ``ErrorResponse``."""
    text = {400: "Bad request", 401: "Not signed in, or the session expired", 403: "Signed in, but not allowed",
            404: "Not found", 409: "Conflict with existing data", 413: "Result too large",
            422: "A parameter or the body is invalid", 429: "Too many attempts", 501: "Not implemented",
            503: "Database unavailable"}
    return {code: {"model": ErrorResponse, "description": text.get(code, "Error")} for code in codes}
