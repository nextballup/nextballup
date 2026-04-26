from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RequestEmailVerificationRequest(BaseModel):
    """Request body for `POST /auth/email/verify/request`.

    Empty by design — the user is identified by the auth cookie. We still
    accept (and require) a body so the endpoint can grow option fields
    later without breaking clients.
    """

    model_config = ConfigDict(extra="forbid")


class RequestEmailVerificationResponse(BaseModel):
    requested_at: datetime
    expires_at: datetime
    delivery: str = Field(description="Provider id used to deliver the message.")


class ConfirmEmailVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=8, max_length=256)


class ConfirmEmailVerificationResponse(BaseModel):
    confirmed_at: datetime
    is_verified: bool


class EmailVerificationStatusResponse(BaseModel):
    is_verified: bool
    pending_request: bool
    last_requested_at: datetime | None
    last_confirmed_at: datetime | None
