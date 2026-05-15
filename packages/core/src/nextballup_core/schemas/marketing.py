from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

PilotRoleValue = Literal[
    "head_coach",
    "assistant_coach",
    "trainer",
    "program_director",
    "other",
]


class PilotInterestRequest(BaseModel):
    """Public, unauthenticated pilot-interest submission from the marketing
    site. Fields are deliberately minimal — anything we cannot tie to a
    legitimate triage need stays out of the audit row.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    role: PilotRoleValue
    organization: str | None = Field(default=None, max_length=160)
    message: str | None = Field(default=None, max_length=2000)

    @field_validator("full_name", "organization", "message")
    @classmethod
    def _strip_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class PilotInterestResponse(BaseModel):
    """Intentionally neutral. The marketing site never echoes back the
    submitter's input — that would invite reflected-content abuse — and the
    backend never reveals whether triage email succeeded so failure modes
    cannot be enumerated by attackers."""

    status: Literal["received"] = "received"
