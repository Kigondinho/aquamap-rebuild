from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

Role = Literal["admin", "user"]


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class Verify2FARequest(BaseModel):
    pending_token: str
    otp_code: str = Field(pattern=r"^\d{6}$")


class Resend2FARequest(BaseModel):
    pending_token: str


class LoginResponse(BaseModel):
    status: Literal["authenticated", "pending_2fa"]
    requires_2fa: bool
    pending_token: str | None = None
    expires_in: int | None = None


class CurrentUser(BaseModel):
    user_id: UUID
    email: EmailStr
    full_name: str
    role: Role
