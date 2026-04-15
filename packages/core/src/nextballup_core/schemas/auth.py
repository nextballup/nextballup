from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

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

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return _validate_password(value)

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
    """Response shape for /auth/register per API_SPEC.md."""

    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: UserRole
    created_at: datetime
    access_token: str
    refresh_token: str


class LoginResponse(BaseModel):
    """Response shape for /auth/login per API_SPEC.md."""

    access_token: str
    refresh_token: str
    user: UserPublic


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = None


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
