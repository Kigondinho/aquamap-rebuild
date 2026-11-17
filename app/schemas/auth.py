from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

Role = Literal["admin", "user"]
OtpCode = Field(pattern=r"^\d{6}$", description="The six-digit code from the email")


def check_password_strength(value: str) -> str:
    """8-128 characters (enforced by the field) with at least one letter and one digit."""
    if not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
        raise ValueError("password must contain at least one letter and one digit")
    return value


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a client cannot pass "role" or other extra fields

    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @field_validator("full_name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("full name must have at least 2 characters")
        return value

    @field_validator("password")
    @classmethod
    def _password_has_letter_and_digit(cls, value: str) -> str:
        return check_password_strength(value)

    @model_validator(mode="after")
    def _passwords_match(self) -> RegisterRequest:
        if self.password != self.confirm_password:
            raise ValueError("password and confirm_password do not match")
        return self


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class Verify2FARequest(BaseModel):
    pending_token: str
    otp_code: str = Field(pattern=r"^\d{6}$")


class Resend2FARequest(BaseModel):
    pending_token: str


class CurrentUser(BaseModel):
    user_id: UUID
    email: EmailStr
    full_name: str
    role: Role


class LoginResponse(BaseModel):
    """On success the session is set as an HTTP-only cookie; the token itself is never in the body."""

    status: Literal["authenticated", "pending_2fa"]
    requires_2fa: bool
    user: CurrentUser | None = None
    pending_token: str | None = None
    expires_in: int | None = Field(default=None, description="Seconds until the session expires")


class RegisterResponse(BaseModel):
    message: str
    user: CurrentUser
    verification_required: bool = Field(
        default=False, description="True: a code was emailed and the address must be confirmed before signing in")


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=254)


class VerifyEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=254)
    code: str = OtpCode


class _NewPassword(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _strong_enough(cls, value: str) -> str:
        return check_password_strength(value)

    @model_validator(mode="after")
    def _passwords_match(self) -> _NewPassword:
        if self.new_password != self.confirm_password:
            raise ValueError("new_password and confirm_password do not match")
        return self


class PasswordChangeRequest(_NewPassword):
    current_password: str = Field(min_length=1, max_length=128)


class PasswordResetRequest(_NewPassword):
    email: EmailStr = Field(max_length=254)
    code: str = OtpCode
