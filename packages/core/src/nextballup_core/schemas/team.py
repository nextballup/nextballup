from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
