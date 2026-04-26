from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nextballup_core.enums import (
    InstitutionType,
    Sport,
    TeamLevel,
    TeamRole,
    UserRole,
)


class CreateTeamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    sport: Sport = Sport.BASKETBALL
    level: TeamLevel
    institution: str | None = Field(default=None, max_length=255)
    institution_type: InstitutionType = InstitutionType.NONE
    season: str = Field(min_length=1, max_length=20)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=10)
    conference: str | None = Field(default=None, max_length=255)


class TeamCreatedResponse(BaseModel):
    id: uuid.UUID
    name: str
    sport: Sport
    level: TeamLevel
    institution: str | None
    institution_type: InstitutionType
    season: str
    invite_code: str
    created_at: datetime
    member_count: int


class CreateInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: TeamRole = TeamRole.PLAYER
    max_uses: int = Field(default=20, ge=1, le=1000)
    expires_in_days: int = Field(default=30, ge=1, le=365)


class CreateInviteResponse(BaseModel):
    invite_code: str
    invite_url: str
    expires_at: datetime
    remaining_uses: int
    role: TeamRole


class TeamPrivacyConsentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    consent_source: str = Field(default="written_permission", min_length=1, max_length=64)
    covers_video_uploads: bool = True
    covers_cv_processing: bool = True
    commercial_ml_training_allowed: bool = False
    minors_authorized: bool = False
    athlete_pii_authorized: bool = True
    evidence_uri: str | None = Field(default=None, min_length=1, max_length=1024)
    evidence_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    expires_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("label", "consent_source")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("evidence_uri", "notes")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("evidence_sha256")
    @classmethod
    def _normalize_evidence_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
            raise ValueError("evidence_sha256 must be a 64-character hex SHA-256 digest")
        return normalized

    @model_validator(mode="after")
    def _require_evidence_pointer(self) -> TeamPrivacyConsentCreate:
        if self.evidence_uri is None and self.evidence_sha256 is None:
            raise ValueError("either evidence_uri or evidence_sha256 is required")
        return self


class TeamPrivacyConsentResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    recorded_by: uuid.UUID | None
    label: str
    consent_source: str
    covers_video_uploads: bool
    covers_cv_processing: bool
    commercial_ml_training_allowed: bool
    minors_authorized: bool
    athlete_pii_authorized: bool
    evidence_uri: str | None
    evidence_sha256: str | None
    effective_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    is_active: bool
    created_at: datetime


class TeamPrivacyConsentListResponse(BaseModel):
    consents: list[TeamPrivacyConsentResponse]
    total: int


class JoinTeamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invite_code: str = Field(min_length=4, max_length=20)
    jersey_number: int | None = Field(default=None, ge=0, le=99)


class TeamMemberSummary(BaseModel):
    user_id: uuid.UUID
    full_name: str
    role: UserRole
    team_role: TeamRole
    jersey_number: int | None
    joined_at: datetime


class TeamSummary(BaseModel):
    id: uuid.UUID
    name: str
    sport: Sport
    level: TeamLevel
    institution: str | None
    institution_type: InstitutionType
    season: str
    invite_code: str | None


class TeamDetailResponse(TeamSummary):
    my_team_role: TeamRole
    members: list[TeamMemberSummary]
    member_count: int


class TeamMembersResponse(BaseModel):
    members: list[TeamMemberSummary]
    total: int


class JoinTeamResponse(TeamSummary):
    membership: TeamMemberSummary


class TeamListEntry(BaseModel):
    """Entry in `GET /teams` — includes the caller's role-in-team + counts.

    `invite_code` is only populated when the caller is a coach on that team.
    Players don't need to see codes they aren't allowed to hand out."""

    id: uuid.UUID
    name: str
    sport: Sport
    level: TeamLevel
    institution: str | None
    institution_type: InstitutionType
    season: str
    invite_code: str | None
    my_team_role: TeamRole
    member_count: int
    game_count: int


class TeamListResponse(BaseModel):
    teams: list[TeamListEntry]
