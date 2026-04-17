"""Local demo seed — reproducible bootstrap for a fresh checkout.

Run with:

    uv run python -m nextballup_api.seed

Creates (idempotently):
  * `coach@demo.local`   / `DemoPass123!` — head coach of the demo team
  * `player@demo.local`  / `DemoPass123!` — player on the demo team
  * Team "Demo Varsity"  (high_school, 2025-26) with a fixed invite code
  * One scheduled game vs. "Westside" next Friday

Uses the **owner** `DATABASE_URL` so inserts bypass FORCE RLS — this is a
dev/local tool, never invoke against staging/production.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, date, datetime, timedelta

import bcrypt
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nextballup_core.enums import (
    GameStatus,
    GameType,
    InstitutionType,
    Sport,
    TeamLevel,
    TeamRole,
    UserRole,
)
from nextballup_core.settings import get_settings
from nextballup_db.models.game import Game
from nextballup_db.models.team import Team, TeamMembership
from nextballup_db.models.user import User

COACH_EMAIL = "coach@demo.local"
PLAYER_EMAIL = "player@demo.local"
DEMO_PASSWORD = "DemoPass123!"  # dev-only — never ships to deployed envs
TEAM_NAME = "Demo Varsity"
TEAM_SEASON = "2025-26"
# Stable invite code so docs can reference it and re-runs don't rotate it.
TEAM_INVITE_CODE = "NBU-DEMO-25"
_LOCAL_DB_HOSTS = {"127.0.0.1", "localhost", "::1", None}


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def _upsert_user(
    session: AsyncSession,
    *,
    email: str,
    full_name: str,
    role: UserRole,
    password_hash: str,
) -> User:
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        existing.full_name = full_name
        existing.role = role
        existing.is_active = True
        existing.is_verified = True
        try:
            password_matches = bcrypt.checkpw(
                DEMO_PASSWORD.encode("utf-8"), existing.password_hash.encode("utf-8")
            )
        except ValueError:
            password_matches = False
        if not password_matches:
            existing.password_hash = password_hash
        return existing
    user = User(
        email=email,
        password_hash=password_hash,
        full_name=full_name,
        role=role,
        is_active=True,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _upsert_team(session: AsyncSession) -> Team:
    existing = await session.scalar(select(Team).where(Team.invite_code == TEAM_INVITE_CODE))
    if existing is None:
        existing = await session.scalar(
            select(Team).where(Team.name == TEAM_NAME, Team.season == TEAM_SEASON)
        )
    if existing is not None:
        existing.name = TEAM_NAME
        existing.sport = Sport.BASKETBALL
        existing.level = TeamLevel.HIGH_SCHOOL
        existing.institution = "Demo High School"
        existing.institution_type = InstitutionType.K12_SCHOOL
        existing.season = TEAM_SEASON
        existing.city = "Seattle"
        existing.state = "WA"
        existing.conference = "Demo Conference"
        existing.invite_code = TEAM_INVITE_CODE
        existing.is_active = True
        return existing
    team = Team(
        name=TEAM_NAME,
        sport=Sport.BASKETBALL,
        level=TeamLevel.HIGH_SCHOOL,
        institution="Demo High School",
        institution_type=InstitutionType.K12_SCHOOL,
        season=TEAM_SEASON,
        city="Seattle",
        state="WA",
        conference="Demo Conference",
        invite_code=TEAM_INVITE_CODE,
        is_active=True,
    )
    session.add(team)
    await session.flush()
    return team


async def _upsert_membership(
    session: AsyncSession,
    *,
    team: Team,
    user: User,
    team_role: TeamRole,
    jersey_number: int | None,
) -> None:
    existing = await session.scalar(
        select(TeamMembership).where(
            TeamMembership.team_id == team.id,
            TeamMembership.user_id == user.id,
        )
    )
    if existing is not None:
        existing.team_role = team_role
        existing.jersey_number = jersey_number
        existing.is_active = True
        return
    session.add(
        TeamMembership(
            team_id=team.id,
            user_id=user.id,
            team_role=team_role,
            jersey_number=jersey_number,
            is_active=True,
        )
    )
    await session.flush()


def _next_friday(today: date) -> date:
    # weekday(): Monday=0 .. Sunday=6. Friday=4.
    delta = (4 - today.weekday()) % 7 or 7
    return today + timedelta(days=delta)


async def _upsert_demo_game(session: AsyncSession, *, team: Team) -> Game:
    existing = await session.scalar(
        select(Game).where(
            Game.team_id == team.id,
            Game.opponent_name == "Westside",
            Game.game_type == GameType.REGULAR_SEASON,
        )
    )
    if existing is not None:
        existing.game_type = GameType.REGULAR_SEASON
        existing.date = _next_friday(datetime.now(tz=UTC).date())
        existing.location = "Demo High School Gym"
        existing.is_home = True
        existing.status = GameStatus.SCHEDULED
        return existing
    game = Game(
        team_id=team.id,
        opponent_name="Westside",
        game_type=GameType.REGULAR_SEASON,
        date=_next_friday(datetime.now(tz=UTC).date()),
        location="Demo High School Gym",
        is_home=True,
        status=GameStatus.SCHEDULED,
    )
    session.add(game)
    await session.flush()
    return game


def _ensure_seed_target_is_local() -> None:
    settings = get_settings()
    if settings.app_env in ("staging", "production"):
        print(
            f"[seed] refusing to run in app_env={settings.app_env}; "
            "seed is a dev-only bootstrap tool.",
            file=sys.stderr,
        )
        sys.exit(2)
    host = make_url(settings.database_url).host
    if host not in _LOCAL_DB_HOSTS and os.environ.get("NBU_ALLOW_NONLOCAL_SEED") != "1":
        print(
            "[seed] refusing to run against a non-local DATABASE_URL host; "
            "set NBU_ALLOW_NONLOCAL_SEED=1 only when you intentionally want to seed "
            "a remote development database.",
            file=sys.stderr,
        )
        sys.exit(2)


async def seed_demo_state(session: AsyncSession) -> tuple[User, User, Team, Game]:
    """Apply the demo-state upserts against an open session.

    The caller owns the transaction lifecycle — this function only flushes so
    relationships are populated; `session.commit()` (or the outer transaction)
    is the caller's responsibility. Exposed separately from `_run` so tests
    can drive it inside their own transaction and roll everything back.
    """
    password_hash = _hash(DEMO_PASSWORD)
    coach = await _upsert_user(
        session,
        email=COACH_EMAIL,
        full_name="Demo Coach",
        role=UserRole.COACH,
        password_hash=password_hash,
    )
    player = await _upsert_user(
        session,
        email=PLAYER_EMAIL,
        full_name="Demo Player",
        role=UserRole.PLAYER,
        password_hash=password_hash,
    )
    team = await _upsert_team(session)
    await _upsert_membership(
        session,
        team=team,
        user=coach,
        team_role=TeamRole.HEAD_COACH,
        jersey_number=None,
    )
    await _upsert_membership(
        session,
        team=team,
        user=player,
        team_role=TeamRole.PLAYER,
        jersey_number=23,
    )
    game = await _upsert_demo_game(session, team=team)
    return coach, player, team, game


async def _run() -> None:
    _ensure_seed_target_is_local()
    settings = get_settings()

    # Use the owner URL directly — seeding needs DDL-adjacent privileges and
    # must bypass FORCE RLS on tenant tables.
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with sessionmaker() as session, session.begin():
            _, _, team, game = await seed_demo_state(session)
    finally:
        await engine.dispose()

    print("[seed] demo state ready:")
    print(f"  coach:       {COACH_EMAIL} / {DEMO_PASSWORD}")
    print(f"  player:      {PLAYER_EMAIL} / {DEMO_PASSWORD}")
    print(f"  team:        {team.name} (invite code: {team.invite_code})")
    print(
        f"  game:        vs. {game.opponent_name} on {game.date.isoformat()} ({game.status.value})"
    )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
