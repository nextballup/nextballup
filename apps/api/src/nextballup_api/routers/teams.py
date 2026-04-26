from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
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
    require_verified_account,
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
    TeamListEntry,
    TeamListResponse,
    TeamMembersResponse,
    TeamMemberSummary,
    TeamPrivacyConsentCreate,
    TeamPrivacyConsentListResponse,
    TeamPrivacyConsentResponse,
)
from nextballup_core.settings import Settings
from nextballup_db.models.game import Game
from nextballup_db.models.team import Team, TeamInvite, TeamMembership, TeamPrivacyConsent
from nextballup_db.models.user import User

router = APIRouter(prefix="/teams", tags=["teams"])

_INVITE_LOOKUP_RETRIES = 5
_PLAYER_SEAT_ROLES: frozenset[TeamRole] = frozenset({TeamRole.PLAYER, TeamRole.CAPTAIN})
_STAFF_SEAT_ROLES: frozenset[TeamRole] = frozenset(
    {TeamRole.HEAD_COACH, TeamRole.ASSISTANT_COACH, TeamRole.MANAGER}
)
_COACH_SEAT_ROLES: frozenset[TeamRole] = frozenset({TeamRole.HEAD_COACH, TeamRole.ASSISTANT_COACH})


def _can_view_invite_code(*, current_user: User, team_role: TeamRole | None) -> bool:
    if current_user.role is UserRole.ADMIN:
        return True
    return team_role in _COACH_SEAT_ROLES


def _team_summary_fields(team: Team, *, invite_code: str | None) -> dict[str, object]:
    return {
        "id": team.id,
        "name": team.name,
        "sport": team.sport,
        "level": team.level,
        "institution": team.institution,
        "institution_type": team.institution_type,
        "season": team.season,
        "invite_code": invite_code,
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


def _privacy_consent_response(
    consent: TeamPrivacyConsent,
    *,
    now: datetime | None = None,
) -> TeamPrivacyConsentResponse:
    resolved_now = now or datetime.now(tz=UTC)
    return TeamPrivacyConsentResponse(
        id=consent.id,
        team_id=consent.team_id,
        recorded_by=consent.recorded_by,
        label=consent.label,
        consent_source=consent.consent_source,
        covers_video_uploads=consent.covers_video_uploads,
        covers_cv_processing=consent.covers_cv_processing,
        commercial_ml_training_allowed=consent.commercial_ml_training_allowed,
        minors_authorized=consent.minors_authorized,
        athlete_pii_authorized=consent.athlete_pii_authorized,
        evidence_uri=consent.evidence_uri,
        evidence_sha256=consent.evidence_sha256,
        effective_at=consent.effective_at,
        expires_at=consent.expires_at,
        revoked_at=consent.revoked_at,
        is_active=(
            consent.revoked_at is None
            and consent.effective_at <= resolved_now
            and (consent.expires_at is None or consent.expires_at > resolved_now)
        ),
        created_at=consent.created_at,
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


@router.get("", response_model=TeamListResponse)
async def list_my_teams(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TeamListResponse:
    """List teams the caller currently belongs to.

    The team picker on the frontend is the only required caller, so we keep
    the contract minimal: one entry per active membership, with member/game
    counts and the caller's team role. `invite_code` is only disclosed to
    coaches — players don't need to see codes they aren't allowed to hand
    out.

    RLS enforcement: `team_memberships_select_access` admits rows where
    `user_id = app.current_user_id`, which `get_current_user` already set.
    `teams_select_access` and `games_select_access` admit via the membership
    EXISTS check, so we do *not* need to bind per-team `app.current_team_id`
    to read the summary. Member counting does need per-team context (the
    membership policy returns only self-rows otherwise), so that runs in a
    small N+1 loop keyed by team id -- at typical user scales (1-5 teams)
    the extra round-trips are negligible.
    """
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()

    my_memberships_stmt = (
        select(TeamMembership, Team)
        .join(Team, Team.id == TeamMembership.team_id)
        .where(
            TeamMembership.user_id == current_user.id,
            TeamMembership.is_active.is_(True),
            Team.is_active.is_(True),
            Team.deleted_at.is_(None),
        )
        .order_by(Team.name)
    )
    rows = (await session.execute(my_memberships_stmt)).all()

    entries: list[TeamListEntry] = []
    for membership, team in rows:
        # Per-team context so team_memberships_select_access admits every
        # active member of this team, not just self.
        await set_tenant_context(session, team.id)
        member_count = int(
            await session.scalar(
                select(func.count())
                .select_from(TeamMembership)
                .where(
                    TeamMembership.team_id == team.id,
                    TeamMembership.is_active.is_(True),
                )
            )
            or 0
        )
        game_count = int(
            await session.scalar(
                select(func.count()).select_from(Game).where(Game.team_id == team.id)
            )
            or 0
        )
        entries.append(
            TeamListEntry(
                id=team.id,
                name=team.name,
                sport=team.sport,
                level=team.level,
                institution=team.institution,
                institution_type=team.institution_type,
                season=team.season,
                invite_code=(
                    team.invite_code
                    if _can_view_invite_code(
                        current_user=current_user,
                        team_role=membership.team_role,
                    )
                    else None
                ),
                my_team_role=membership.team_role,
                member_count=member_count,
                game_count=game_count,
            )
        )
    await clear_tenant_context(session)
    return TeamListResponse(teams=entries)


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
    settings: Settings = Depends(get_app_settings),
) -> TeamCreatedResponse:
    require_user_role(current_user, UserRole.COACH, UserRole.ADMIN)
    require_verified_account(current_user, settings=settings)

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
        **_team_summary_fields(team, invite_code=team.invite_code),
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
    require_verified_account(current_user, settings=settings)
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


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> None:
    require_verified_account(current_user, settings=settings)
    await clear_join_invite_context(session)
    await set_tenant_context(session, team_id)

    team = await session.get(Team, team_id)
    if team is None or not team.is_active:
        return None
    await require_team_coach(session, user=current_user, team_id=team.id)
    if team.deleted_at is not None:
        return None

    deleted_at = datetime.now(tz=UTC)
    team.deleted_at = deleted_at
    await write_audit(
        session,
        action=AuditAction.TEAM_DELETED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="team",
        resource_id=team.id,
        team_id=team.id,
        extra={"deleted_at": deleted_at.isoformat()},
    )
    await session.commit()
    return None


@router.post(
    "/{team_id}/privacy-consents",
    response_model=TeamPrivacyConsentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_privacy_consent(
    team_id: uuid.UUID,
    payload: TeamPrivacyConsentCreate,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> TeamPrivacyConsentResponse:
    require_verified_account(current_user, settings=settings)
    await clear_join_invite_context(session)
    await set_tenant_context(session, team_id)
    team = await get_team_or_404(session, team_id)
    await require_team_coach(session, user=current_user, team_id=team.id)

    consent = TeamPrivacyConsent(
        team_id=team.id,
        recorded_by=current_user.id,
        label=payload.label,
        consent_source=payload.consent_source,
        covers_video_uploads=payload.covers_video_uploads,
        covers_cv_processing=payload.covers_cv_processing,
        commercial_ml_training_allowed=payload.commercial_ml_training_allowed,
        minors_authorized=payload.minors_authorized,
        athlete_pii_authorized=payload.athlete_pii_authorized,
        evidence_uri=payload.evidence_uri,
        evidence_sha256=payload.evidence_sha256,
        expires_at=payload.expires_at,
        notes=payload.notes,
    )
    session.add(consent)
    await session.flush()

    await write_audit(
        session,
        action=AuditAction.TEAM_PRIVACY_CONSENT_RECORDED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="team_privacy_consent",
        resource_id=consent.id,
        team_id=team.id,
        extra={
            "covers_video_uploads": consent.covers_video_uploads,
            "covers_cv_processing": consent.covers_cv_processing,
            "commercial_ml_training_allowed": consent.commercial_ml_training_allowed,
            "minors_authorized": consent.minors_authorized,
            "has_evidence_uri": consent.evidence_uri is not None,
            "has_evidence_sha256": consent.evidence_sha256 is not None,
        },
    )
    await session.commit()
    await session.refresh(consent)
    return _privacy_consent_response(consent)


@router.get(
    "/{team_id}/privacy-consents",
    response_model=TeamPrivacyConsentListResponse,
)
async def list_privacy_consents(
    team_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TeamPrivacyConsentListResponse:
    await clear_join_invite_context(session)
    await set_tenant_context(session, team_id)
    team = await get_team_or_404(session, team_id)
    await require_team_member(session, user=current_user, team_id=team.id)

    now = datetime.now(tz=UTC)
    rows = await session.execute(
        select(TeamPrivacyConsent)
        .where(TeamPrivacyConsent.team_id == team.id)
        .order_by(TeamPrivacyConsent.created_at.desc())
    )
    consents = [_privacy_consent_response(row, now=now) for row in rows.scalars()]
    return TeamPrivacyConsentListResponse(consents=consents, total=len(consents))


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
    require_verified_account(current_user, settings=settings)

    await clear_tenant_context(session)
    await set_join_invite_context(session, code)

    team = await session.scalar(select(Team).where(Team.invite_code == code))
    invite: TeamInvite | None = None
    if team is None:
        invite = await session.scalar(
            select(TeamInvite).where(TeamInvite.invite_code == code).with_for_update()
        )
        if invite is not None:
            await set_tenant_context(session, invite.team_id)
            team = await session.get(Team, invite.team_id)
    else:
        await set_tenant_context(session, team.id)
    await clear_join_invite_context(session)

    if team is None or not team.is_active or team.deleted_at is not None:
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
        **_team_summary_fields(
            team,
            invite_code=(
                team.invite_code
                if _can_view_invite_code(
                    current_user=current_user,
                    team_role=membership.team_role,
                )
                else None
            ),
        ),
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
    my_membership = next(
        (
            membership
            for membership in full.memberships
            if membership.user_id == current_user.id and membership.is_active
        ),
        None,
    )
    if my_membership is None:  # pragma: no cover -- guarded by require_team_member above
        raise ForbiddenError("Membership required", code=ErrorCode.FORBIDDEN)
    return TeamDetailResponse(
        **_team_summary_fields(
            full,
            invite_code=(
                full.invite_code
                if _can_view_invite_code(
                    current_user=current_user,
                    team_role=my_membership.team_role,
                )
                else None
            ),
        ),
        my_team_role=my_membership.team_role,
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
