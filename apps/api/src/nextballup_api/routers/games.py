from __future__ import annotations

import uuid
from datetime import date as _date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api.audit import write_audit
from nextballup_api.deps import get_current_user, get_db
from nextballup_api.permissions import (
    get_team_or_404,
    require_team_coach,
    require_team_member,
    require_user_role,
)
from nextballup_api.tenant import (
    clear_join_invite_context,
    clear_tenant_context,
    set_tenant_context,
)
from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import GameStatus, GameType, UserRole
from nextballup_core.errors import ForbiddenError, NotFoundError
from nextballup_core.schemas.game import (
    CreateGameRequest,
    GameListResponse,
    GameSummary,
    UpdateGameRequest,
)
from nextballup_core.schemas.video import VideoListItem, VideoListResponse
from nextballup_db.models.game import Game
from nextballup_db.models.team import TeamMembership
from nextballup_db.models.user import User
from nextballup_db.models.video import Video

router = APIRouter(prefix="/games", tags=["games"])

# Game statuses that should not be flipped back to scheduled/processing by a
# regular coach. Admin role bypasses this guard for ops fix-ups.
_TERMINAL_GAME_STATUSES: frozenset[GameStatus] = frozenset(
    {GameStatus.COMPLETED, GameStatus.FAILED}
)


async def _load_game(session: AsyncSession, game_id: uuid.UUID) -> Game | None:
    result = await session.execute(
        select(Game).where(Game.id == game_id).execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


@router.post("", response_model=GameSummary, status_code=status.HTTP_201_CREATED)
async def create_game(
    payload: CreateGameRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GameSummary:
    require_user_role(current_user, UserRole.COACH, UserRole.ADMIN)
    await clear_join_invite_context(session)
    await set_tenant_context(session, payload.team_id)
    team = await get_team_or_404(session, payload.team_id)
    await require_team_coach(session, user=current_user, team_id=team.id)

    game = Game(
        team_id=team.id,
        opponent_name=payload.opponent_name,
        game_type=payload.game_type,
        date=payload.date,
        time=payload.time,
        location=payload.location,
        is_home=payload.is_home,
        periods=payload.periods,
        period_length_minutes=payload.period_length_minutes,
        notes=payload.notes,
        status=GameStatus.SCHEDULED,
    )
    session.add(game)
    await session.flush()

    await write_audit(
        session,
        action=AuditAction.GAME_CREATED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="game",
        resource_id=game.id,
        team_id=team.id,
        extra={"game_type": game.game_type.value, "date": game.date.isoformat()},
    )
    await session.commit()
    await session.refresh(game)
    return GameSummary.model_validate(game)


@router.get("", response_model=GameListResponse)
async def list_games(
    request: Request,
    team_id: Annotated[uuid.UUID | None, Query()] = None,
    game_status: Annotated[GameStatus | None, Query(alias="status")] = None,
    game_type: Annotated[GameType | None, Query()] = None,
    date_from: Annotated[_date | None, Query(alias="from")] = None,
    date_to: Annotated[_date | None, Query(alias="to")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GameListResponse:
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()

    if team_id is not None:
        # Specific-team query: bind tenant context and verify membership so
        # the FORCE-RLS policies on the games table actually filter under
        # production roles, even though we layer an explicit app-level guard.
        await set_tenant_context(session, team_id)
        await require_team_member(session, user=current_user, team_id=team_id)
        team_filter = Game.team_id == team_id
    elif current_user.role is UserRole.ADMIN:
        # Admin role: list across all tenants. The user_role GUC is left at
        # access-token defaults; the games SELECT policy admits admin reads.
        team_filter = None
    else:
        # No team specified — restrict to teams the caller actively belongs
        # to. Subquery returns the user's active team ids for the IN filter.
        membership_subq = select(TeamMembership.team_id).where(
            TeamMembership.user_id == current_user.id,
            TeamMembership.is_active.is_(True),
        )
        team_filter = Game.team_id.in_(membership_subq)

    filters = []
    if team_filter is not None:
        filters.append(team_filter)
    if game_status is not None:
        filters.append(Game.status == game_status)
    if game_type is not None:
        filters.append(Game.game_type == game_type)
    if date_from is not None:
        filters.append(Game.date >= date_from)
    if date_to is not None:
        filters.append(Game.date <= date_to)
    where_clause = and_(*filters) if filters else None

    base = select(Game)
    if where_clause is not None:
        base = base.where(where_clause)

    count_stmt = select(func.count()).select_from(Game)
    if where_clause is not None:
        count_stmt = count_stmt.where(where_clause)
    total = int(await session.scalar(count_stmt) or 0)

    rows = await session.execute(
        base.order_by(Game.date.desc(), Game.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    games = [GameSummary.model_validate(g) for g in rows.scalars().all()]
    return GameListResponse(
        games=games,
        total=total,
        page=page,
        per_page=per_page,
        has_next=(page * per_page) < total,
    )


@router.get("/{game_id}", response_model=GameSummary)
async def get_game(
    game_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GameSummary:
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    game = await _load_game(session, game_id)
    if game is None:
        raise NotFoundError("Game not found", code=ErrorCode.GAME_NOT_FOUND)
    await set_tenant_context(session, game.team_id)
    await require_team_member(session, user=current_user, team_id=game.team_id)
    return GameSummary.model_validate(game)


@router.get("/{game_id}/videos", response_model=VideoListResponse)
async def list_game_videos(
    game_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VideoListResponse:
    """List videos attached to a game. Any team member may read — this surface
    does not issue signed playback URLs, so it is safe for every member."""
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    game = await _load_game(session, game_id)
    if game is None:
        raise NotFoundError("Game not found", code=ErrorCode.GAME_NOT_FOUND)
    await set_tenant_context(session, game.team_id)
    await require_team_member(session, user=current_user, team_id=game.team_id)

    rows = await session.execute(
        select(Video).where(Video.game_id == game.id).order_by(Video.created_at.desc())
    )
    videos = rows.scalars().all()
    items = [
        VideoListItem(
            id=v.id,
            filename=v.filename,
            status=v.status,
            file_size_bytes=v.file_size_bytes,
            duration_seconds=v.duration_seconds,
            camera_position=v.camera_position,
            camera_height=v.camera_height,
            created_at=v.created_at,
        )
        for v in videos
    ]
    return VideoListResponse(videos=items, total=len(items))


@router.patch("/{game_id}", response_model=GameSummary)
async def update_game(
    game_id: uuid.UUID,
    payload: UpdateGameRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GameSummary:
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    game = await _load_game(session, game_id)
    if game is None:
        raise NotFoundError("Game not found", code=ErrorCode.GAME_NOT_FOUND)
    await set_tenant_context(session, game.team_id)
    await require_team_coach(session, user=current_user, team_id=game.team_id)

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return GameSummary.model_validate(game)

    new_status = updates.get("status")
    if (
        new_status is not None
        and game.status in _TERMINAL_GAME_STATUSES
        and new_status is not game.status
        and current_user.role is not UserRole.ADMIN
    ):
        raise ForbiddenError(
            "Cannot change status of a terminal game; ask an admin to reopen",
            code=ErrorCode.GAME_TERMINAL_STATUS,
        )

    for field, value in updates.items():
        setattr(game, field, value)
    await session.flush()
    await write_audit(
        session,
        action=AuditAction.GAME_UPDATED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="game",
        resource_id=game.id,
        team_id=game.team_id,
        extra={"fields": sorted(updates.keys())},
    )
    await session.commit()
    await session.refresh(game)
    return GameSummary.model_validate(game)
