from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from nextballup_api.audit import write_audit
from nextballup_api.deps import get_app_settings, get_current_user, get_db
from nextballup_api.permissions import (
    get_team_or_404,
    require_team_coach,
    require_team_member,
    require_user_role,
)
from nextballup_api.security.invite_code import generate_invite_code
from nextballup_api.security.rate_limit import enforce_rate_limit
from nextballup_api.tenant import (
    clear_join_invite_context,
    clear_tenant_context,
    set_join_invite_context,
    set_tenant_context,
)
from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import TeamRole, UserRole
from nextballup_core.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from nextballup_core.schemas.team import (
    CreateInviteRequest,
    CreateInviteResponse,
    CreateTeamRequest,
    JoinTeamRequest,
    JoinTeamResponse,
    TeamCreatedResponse,
    TeamDetailResponse,
    TeamMembersResponse,
    TeamMemberSummary,
)
from nextballup_core.settings import Settings
from nextballup_db.models.team import Team, TeamInvite, TeamMembership
from nextballup_db.models.user import User

router = APIRouter(prefix="/teams", tags=["teams"])

_INVITE_LOOKUP_RETRIES = 5
_PLAYER_SEAT_ROLES: frozenset[TeamRole] = frozenset({TeamRole.PLAYER, TeamRole.CAPTAIN})
_STAFF_SEAT_ROLES: frozenset[TeamRole] = frozenset(
    {TeamRole.HEAD_COACH, TeamRole.ASSISTANT_COACH, TeamRole.MANAGER}
)


def _team_summary_fields(team: Team) -> dict[str, object]:
    return {
        "id": team.id,
        "name": team.name,
        "sport": team.sport,
        "level": team.level,
        "institution": team.institution,
        "institution_type": team.institution_type,
        "season": team.season,
        "invite_code": team.invite_code,
    }


def _member_summary(membership: TeamMembership, user: User) -> TeamMemberSummary:
    return TeamMemberSummary(
        user_id=user.id,
        full_name=user.full_name,
        role=user.role,
        team_role=membership.team_role,
        jersey_number=membership.jersey_number,
        joined_at=membership.joined_at,
    )


def _normalize_invite_code(value: str) -> str:
    return value.strip().upper()


def _invite_url(settings: Settings, invite_code: str) -> str:
    return f"{settings.frontend_app_url.rstrip('/')}/join/{invite_code}"


def _team_role_allowed_for_user(*, user_role: UserRole, team_role: TeamRole) -> bool:
    if user_role is UserRole.ADMIN:
        return True
    if team_role in _PLAYER_SEAT_ROLES:
        return user_role is UserRole.PLAYER
    return user_role is UserRole.COACH


async def _generate_unique_invite_code(session: AsyncSession) -> str:
    """Try a handful of random codes; the DB unique constraint guards against
    races. 32^10 keyspace — collisions are extremely rare in practice."""
    for _ in range(_INVITE_LOOKUP_RETRIES):
        code = generate_invite_code()
        existing = await session.scalar(select(Team.id).where(Team.invite_code == code))
        if existing is not None:
            continue
        existing_invite = await session.scalar(
            select(TeamInvite.id).where(TeamInvite.invite_code == code)
        )
        if existing_invite is None:
            return code
    raise AppError(
        "Could not allocate a unique invite code; please retry",
        code=ErrorCode.INTERNAL_ERROR,
        status_code=503,
    )


def _audit_join_failure(reason: str) -> AppError:
    if reason == ErrorCode.INVITE_NOT_FOUND:
        return NotFoundError("Invite code not found", code=ErrorCode.INVITE_NOT_FOUND)
    if reason == ErrorCode.INVITE_EXPIRED:
        return ConflictError("Invite has expired", code=ErrorCode.INVITE_EXPIRED)
    if reason == ErrorCode.INVITE_EXHAUSTED:
        return ConflictError("Invite has reached its usage limit", code=ErrorCode.INVITE_EXHAUSTED)
    if reason == ErrorCode.INVITE_INACTIVE:
        return ConflictError("Invite is no longer active", code=ErrorCode.INVITE_INACTIVE)
    if reason == ErrorCode.INVITE_ROLE_MISMATCH:
        return ForbiddenError(
            "This invite is not valid for your account type",
            code=ErrorCode.INVITE_ROLE_MISMATCH,
        )
    if reason == ErrorCode.JERSEY_NUMBER_REQUIRED:
        return ValidationFailedError(
            "Jersey number is required for player members",
            code=ErrorCode.JERSEY_NUMBER_REQUIRED,
        )
    if reason == ErrorCode.JERSEY_NUMBER_TAKEN:
        return ConflictError(
            "Jersey number is already taken on this team",
            code=ErrorCode.JERSEY_NUMBER_TAKEN,
        )
    if reason == ErrorCode.ALREADY_MEMBER:
        return ConflictError("You are already a member of this team", code=ErrorCode.ALREADY_MEMBER)
    return AppError("Could not join team", code=reason)


async def _record_join_failure(
    session: AsyncSession,
    *,
    request: Request,
    current_user: User,
    invite_code: str,
    team_id: uuid.UUID | None,
    reason: str,
) -> None:
    await write_audit(
        session,
        action=AuditAction.TEAM_JOIN_FAILED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="team",
        resource_id=team_id,
        team_id=team_id,
        extra={"reason": reason, "invite_code": invite_code},
    )
    await session.commit()


async def _load_team_with_memberships(session: AsyncSession, team_id: uuid.UUID) -> Team | None:
    result = await session.execute(
        select(Team)
        .where(Team.id == team_id)
        .options(selectinload(Team.memberships).selectinload(TeamMembership.user))
    )
    return result.scalar_one_or_none()


@router.post(
    "",
    response_model=TeamCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_team(
    payload: CreateTeamRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TeamCreatedResponse:
    require_user_role(current_user, UserRole.COACH, UserRole.ADMIN)

    invite_code = await _generate_unique_invite_code(session)
    team_id = uuid.uuid4()
    await clear_join_invite_context(session)
    await set_tenant_context(session, team_id)

    team = Team(
        id=team_id,
        name=payload.name,
        sport=payload.sport,
        level=payload.level,
        institution=payload.institution,
        institution_type=payload.institution_type,
        season=payload.season,
        city=payload.city,
        state=payload.state,
        conference=payload.conference,
        invite_code=invite_code,
    )
    session.add(team)
    await session.flush()

    membership = TeamMembership(
        team_id=team.id,
        user_id=current_user.id,
        team_role=TeamRole.HEAD_COACH,
    )
    session.add(membership)

    await write_audit(
        session,
        action=AuditAction.TEAM_CREATED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="team",
        resource_id=team.id,
        team_id=team.id,
        extra={"sport": team.sport.value, "level": team.level.value},
    )
    await session.commit()
    await session.refresh(team)

    return TeamCreatedResponse(
        **_team_summary_fields(team),
        created_at=team.created_at,
        member_count=1,
    )


@router.post(
    "/{team_id}/invite",
    response_model=CreateInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite(
    team_id: uuid.UUID,
    payload: CreateInviteRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> CreateInviteResponse:
    await clear_join_invite_context(session)
    await set_tenant_context(session, team_id)
    team = await get_team_or_404(session, team_id)
    membership = await require_team_coach(session, user=current_user, team_id=team.id)
    if (
        payload.role is TeamRole.HEAD_COACH
        and current_user.role is not UserRole.ADMIN
        and membership.team_role is not TeamRole.HEAD_COACH
    ):
        raise ForbiddenError("Only a head coach can issue a head coach invite")

    invite_code = await _generate_unique_invite_code(session)
    expires_at = datetime.now(tz=UTC) + timedelta(days=payload.expires_in_days)

    invite = TeamInvite(
        team_id=team.id,
        invite_code=invite_code,
        role=payload.role,
        max_uses=payload.max_uses,
        uses=0,
        expires_at=expires_at,
        created_by=current_user.id,
        is_active=True,
    )
    session.add(invite)
    await session.flush()

    await write_audit(
        session,
        action=AuditAction.TEAM_INVITE_CREATED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="team_invite",
        resource_id=invite.id,
        team_id=team.id,
        extra={"role": payload.role.value, "max_uses": payload.max_uses},
    )
    await session.commit()
    await session.refresh(invite)

    return CreateInviteResponse(
        invite_code=invite.invite_code,
        invite_url=_invite_url(settings, invite.invite_code),
        expires_at=invite.expires_at,
        remaining_uses=invite.max_uses - invite.uses,
        role=invite.role,
    )


@router.post(
    "/join",
    response_model=JoinTeamResponse,
    status_code=status.HTTP_200_OK,
)
async def join_team(
    payload: JoinTeamRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> JoinTeamResponse:
    code = _normalize_invite_code(payload.invite_code)
    await enforce_rate_limit(
        request=request,
        settings=settings,
        scope="team_join",
        subject=str(current_user.id),
        max_attempts=settings.team_join_rate_limit_attempts,
        window_seconds=settings.team_join_rate_limit_window_seconds,
    )

    await clear_tenant_context(session)
    await set_join_invite_context(session, code)

    team = await session.scalar(select(Team).where(Team.invite_code == code))
    invite: TeamInvite | None = None
    if team is None:
        invite = await session.scalar(select(TeamInvite).where(TeamInvite.invite_code == code))
        if invite is not None:
            await set_tenant_context(session, invite.team_id)
            team = await session.get(Team, invite.team_id)
    else:
        await set_tenant_context(session, team.id)
    await clear_join_invite_context(session)

    if team is None or not team.is_active:
        await _record_join_failure(
            session,
            request=request,
            current_user=current_user,
            invite_code=code,
            team_id=None,
            reason=ErrorCode.INVITE_NOT_FOUND,
        )
        raise _audit_join_failure(ErrorCode.INVITE_NOT_FOUND)

    if invite is not None:
        now = datetime.now(tz=UTC)
        if not invite.is_active:
            await _record_join_failure(
                session,
                request=request,
                current_user=current_user,
                invite_code=code,
                team_id=team.id,
                reason=ErrorCode.INVITE_INACTIVE,
            )
            raise _audit_join_failure(ErrorCode.INVITE_INACTIVE)
        if invite.expires_at <= now:
            await _record_join_failure(
                session,
                request=request,
                current_user=current_user,
                invite_code=code,
                team_id=team.id,
                reason=ErrorCode.INVITE_EXPIRED,
            )
            raise _audit_join_failure(ErrorCode.INVITE_EXPIRED)
        if invite.uses >= invite.max_uses:
            await _record_join_failure(
                session,
                request=request,
                current_user=current_user,
                invite_code=code,
                team_id=team.id,
                reason=ErrorCode.INVITE_EXHAUSTED,
            )
            raise _audit_join_failure(ErrorCode.INVITE_EXHAUSTED)

    target_team_role = invite.role if invite is not None else TeamRole.PLAYER
    if not _team_role_allowed_for_user(user_role=current_user.role, team_role=target_team_role):
        await _record_join_failure(
            session,
            request=request,
            current_user=current_user,
            invite_code=code,
            team_id=team.id,
            reason=ErrorCode.INVITE_ROLE_MISMATCH,
        )
        raise _audit_join_failure(ErrorCode.INVITE_ROLE_MISMATCH)

    is_player_seat = target_team_role in _PLAYER_SEAT_ROLES

    existing = await session.scalar(
        select(TeamMembership.id).where(
            TeamMembership.team_id == team.id,
            TeamMembership.user_id == current_user.id,
        )
    )
    if existing is not None:
        await _record_join_failure(
            session,
            request=request,
            current_user=current_user,
            invite_code=code,
            team_id=team.id,
            reason=ErrorCode.ALREADY_MEMBER,
        )
        raise _audit_join_failure(ErrorCode.ALREADY_MEMBER)

    if is_player_seat and current_user.role is UserRole.PLAYER and payload.jersey_number is None:
        await _record_join_failure(
            session,
            request=request,
            current_user=current_user,
            invite_code=code,
            team_id=team.id,
            reason=ErrorCode.JERSEY_NUMBER_REQUIRED,
        )
        raise _audit_join_failure(ErrorCode.JERSEY_NUMBER_REQUIRED)

    if is_player_seat and payload.jersey_number is not None:
        jersey_clash = await session.scalar(
            select(TeamMembership.id).where(
                TeamMembership.team_id == team.id,
                TeamMembership.jersey_number == payload.jersey_number,
            )
        )
        if jersey_clash is not None:
            await _record_join_failure(
                session,
                request=request,
                current_user=current_user,
                invite_code=code,
                team_id=team.id,
                reason=ErrorCode.JERSEY_NUMBER_TAKEN,
            )
            raise _audit_join_failure(ErrorCode.JERSEY_NUMBER_TAKEN)

    membership = TeamMembership(
        team_id=team.id,
        user_id=current_user.id,
        team_role=target_team_role,
        jersey_number=payload.jersey_number if is_player_seat else None,
    )
    session.add(membership)
    if invite is not None:
        invite.uses += 1

    try:
        await session.flush()
    except IntegrityError as exc:
        raise _audit_join_failure(ErrorCode.JERSEY_NUMBER_TAKEN) from exc

    await write_audit(
        session,
        action=AuditAction.TEAM_JOIN_SUCCEEDED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="team_membership",
        resource_id=membership.id,
        team_id=team.id,
        extra={
            "team_role": target_team_role.value,
            "via_invite": invite is not None,
        },
    )
    await session.commit()
    await session.refresh(membership)

    return JoinTeamResponse(
        **_team_summary_fields(team),
        membership=_member_summary(membership, current_user),
    )


@router.get("/{team_id}", response_model=TeamDetailResponse)
async def get_team(
    team_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TeamDetailResponse:
    await clear_join_invite_context(session)
    await set_tenant_context(session, team_id)
    team = await get_team_or_404(session, team_id)
    await require_team_member(session, user=current_user, team_id=team.id)

    full = await _load_team_with_memberships(session, team.id)
    if full is None:  # pragma: no cover -- guarded by get_team_or_404 above
        raise NotFoundError("Team not found", code=ErrorCode.TEAM_NOT_FOUND)

    members = [
        _member_summary(m, m.user) for m in full.memberships if m.is_active and m.user is not None
    ]
    return TeamDetailResponse(
        **_team_summary_fields(full),
        members=members,
        member_count=len(members),
    )


@router.get("/{team_id}/members", response_model=TeamMembersResponse)
async def list_team_members(
    team_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TeamMembersResponse:
    await clear_join_invite_context(session)
    await set_tenant_context(session, team_id)
    team = await get_team_or_404(session, team_id)
    await require_team_member(session, user=current_user, team_id=team.id)

    rows = await session.execute(
        select(TeamMembership, User)
        .join(User, User.id == TeamMembership.user_id)
        .where(
            TeamMembership.team_id == team.id,
            TeamMembership.is_active.is_(True),
        )
        .order_by(TeamMembership.joined_at)
    )
    members = [_member_summary(m, u) for m, u in rows.all()]
    return TeamMembersResponse(members=members, total=len(members))
