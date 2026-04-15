from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import ErrorCode
from nextballup_core.enums import TeamRole, UserRole
from nextballup_core.errors import ForbiddenError, NotFoundError
from nextballup_db.models.team import Team, TeamMembership
from nextballup_db.models.user import User

# Coach-tier roles for team administration. Captain and manager can lead on
# the floor but do not get write access to roster/invite endpoints.
COACH_TEAM_ROLES: frozenset[TeamRole] = frozenset({TeamRole.HEAD_COACH, TeamRole.ASSISTANT_COACH})


def require_user_role(user: User, *roles: UserRole) -> None:
    if user.role not in roles:
        raise ForbiddenError(
            "This action requires a different role",
            details={"required_roles": [r.value for r in roles]},
        )


async def get_team_or_404(session: AsyncSession, team_id: uuid.UUID) -> Team:
    team = await session.get(Team, team_id)
    if team is None or not team.is_active:
        raise NotFoundError("Team not found", code=ErrorCode.TEAM_NOT_FOUND)
    return team


async def get_team_membership(
    session: AsyncSession, *, user_id: uuid.UUID, team_id: uuid.UUID
) -> TeamMembership | None:
    result: TeamMembership | None = await session.scalar(
        select(TeamMembership).where(
            TeamMembership.team_id == team_id,
            TeamMembership.user_id == user_id,
            TeamMembership.is_active.is_(True),
        )
    )
    return result


async def require_team_member(
    session: AsyncSession, *, user: User, team_id: uuid.UUID
) -> TeamMembership:
    """Admin role bypasses team membership checks (operator backstop)."""
    if user.role is UserRole.ADMIN:
        membership = await get_team_membership(session, user_id=user.id, team_id=team_id)
        if membership is not None:
            return membership
        # Synthesize a minimum-privilege view for admin-not-yet-on-team. Note
        # that this membership is not persisted; it lets admin tooling read
        # team data without auto-joining.
        return TeamMembership(
            team_id=team_id,
            user_id=user.id,
            team_role=TeamRole.MANAGER,
        )
    membership = await get_team_membership(session, user_id=user.id, team_id=team_id)
    if membership is None:
        raise ForbiddenError(
            "You are not a member of this team",
            code=ErrorCode.FORBIDDEN,
        )
    return membership


async def require_team_coach(
    session: AsyncSession, *, user: User, team_id: uuid.UUID
) -> TeamMembership:
    if user.role is UserRole.ADMIN:
        return await require_team_member(session, user=user, team_id=team_id)
    membership = await require_team_member(session, user=user, team_id=team_id)
    if membership.team_role not in COACH_TEAM_ROLES:
        raise ForbiddenError(
            "Coach access required for this action",
            details={"required_team_roles": sorted(r.value for r in COACH_TEAM_ROLES)},
        )
    return membership
