from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from nextballup_core.enums import UserRole

# Password rules from API_SPEC.md F1: 8+ chars, at least one number and one uppercase.
_PASSWORD_MIN_LEN = 8
_PASSWORD_MAX_BYTES = 72
_HAS_DIGIT = re.compile(r"\d")
_HAS_UPPER = re.compile(r"[A-Z]")


def _validate_password(value: str) -> str:
    if len(value) < _PASSWORD_MIN_LEN:
        raise ValueError(f"Password must be at least {_PASSWORD_MIN_LEN} characters")
    if not _HAS_DIGIT.search(value):
        raise ValueError("Password must contain at least one digit")
    if not _HAS_UPPER.search(value):
        raise ValueError("Password must contain at least one uppercase letter")
    if len(value.encode("utf-8")) > _PASSWORD_MAX_BYTES:
        raise ValueError(
            f"Password must be {_PASSWORD_MAX_BYTES} UTF-8 bytes or fewer for bcrypt compatibility"
        )
    return value


Password = Annotated[str, Field(min_length=_PASSWORD_MIN_LEN, max_length=128)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: Password
    full_name: str = Field(min_length=1, max_length=255)
    role: UserRole
    phone: str | None = Field(default=None, max_length=20)
    institution: str | None = Field(default=None, max_length=255)
    invite_code: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return _validate_password(value)

    @field_validator("invite_code", mode="before")
    @classmethod
    def _trim_invite_code(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("role")
    @classmethod
    def _no_admin_self_register(cls, value: UserRole) -> UserRole:
        if value is UserRole.ADMIN:
            raise ValueError("admin role cannot be self-registered")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    mfa_code: str | None = Field(default=None, min_length=4, max_length=32)


class TeamMembershipSummary(BaseModel):
    id: uuid.UUID
    name: str
    role_in_team: str


class UserPublic(BaseModel):
    """User shape returned by /auth/login and /auth/me."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: UserRole
    teams: list[TeamMembershipSummary] = Field(default_factory=list)


class RegisterResponse(BaseModel):
    """Response shape for /auth/register.

    Tokens are delivered exclusively via httpOnly cookies (see
    `set_auth_cookies`) — never in the JSON body. Surfacing tokens in JSON
    would let a compromised renderer, extension, or logger exfiltrate a
    live session even though the cookie itself is httpOnly.
    """

    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: UserRole
    created_at: datetime


class LoginResponse(BaseModel):
    """Response shape for /auth/login.

    Cookie-only transport: the browser picks up the access and refresh
    cookies from ``Set-Cookie`` and never sees the JWT itself. Non-browser
    API clients can read the same cookies off the response.
    """

    user: UserPublic


class RefreshRequest(BaseModel):
    """Refresh is cookie-only; the body is required but has no fields.

    We still accept a JSON body because browsers that fire XHR/fetch POSTs
    with `Content-Type: application/json` send an empty object, and keeping
    the model makes future extension (device binding, step-up hints)
    cheap. A legacy JSON refresh_token is rejected by `extra="forbid"`.
    """

    model_config = ConfigDict(extra="forbid")


class RefreshResponse(BaseModel):
    """Successful refresh rotates tokens into the cookie jar.

    The body is intentionally minimal — the browser doesn't need tokens
    echoed back to know the call succeeded.
    """

    refreshed_at: datetime


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class PasswordResetRequestResponse(BaseModel):
    requested_at: datetime
    delivery: str


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=256)
    new_password: Password

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return _validate_password(value)


class PasswordResetConfirmResponse(BaseModel):
    reset_at: datetime


class RegistrationStatusResponse(BaseModel):
    """Whether the public can call /auth/register and how.

    The frontend uses this to render the right UI: hide the CTA when
    registration is disabled, show an invite-code field when invite_only,
    or expose an allowlist hint. The endpoint deliberately does **not**
    leak the configured codes or allowlisted emails.
    """

    mode: Literal["open", "invite_only", "allowlist", "disabled"]
    invite_code_required: bool
    is_open_to_public: bool
