from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Tier = Literal["Low", "Medium", "High", "Severe"]


class ErrorResponse(BaseModel):
    detail: str


class MessageResponse(BaseModel):
    message: str
