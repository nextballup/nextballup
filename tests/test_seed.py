"""Seed bootstrap: idempotency and environment guard.

These tests drive `nextballup_api.seed.seed_demo_state` against the test
database session to prove:

* A fresh run creates the demo coach, player, team, membership, and game.
* A second run is safe — no duplicate users, teams, memberships, or games
  appear (developers need to re-run freely without manual cleanup).
* The CLI wrapper refuses to touch staging/production or non-local DB targets
  unless explicitly overridden.

We drive the inner helper (not `main()`) so writes ride the outer-transaction
rollback in conftest and never persist into the test DB.
"""

from __future__ import annotations

import os

import pytest
from nextballup_api import seed as seed_module
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.enums import GameStatus
from nextballup_db.models.game import Game
from nextballup_db.models.team import Team, TeamMembership
from nextballup_db.models.user import User


@pytest.mark.asyncio(loop_scope="session")
async def test_seed_demo_state_is_idempotent(db_session: AsyncSession) -> None:
    await seed_module.seed_demo_state(db_session)
    await seed_module.seed_demo_state(db_session)

    coach_count = await db_session.scalar(
        select(func.count()).select_from(User).where(User.email == seed_module.COACH_EMAIL)
    )
    player_count = await db_session.scalar(
        select(func.count()).select_from(User).where(User.email == seed_module.PLAYER_EMAIL)
    )
    assert coach_count == 1
    assert player_count == 1

    team = await db_session.scalar(
        select(Team).where(Team.invite_code == seed_module.TEAM_INVITE_CODE)
    )
    assert team is not None

    membership_count = await db_session.scalar(
        select(func.count()).select_from(TeamMembership).where(TeamMembership.team_id == team.id)
    )
    assert membership_count == 2

    game_count = await db_session.scalar(
        select(func.count())
        .select_from(Game)
        .where(Game.team_id == team.id, Game.opponent_name == "Westside")
    )
    assert game_count == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_seed_demo_state_repairs_existing_rows(db_session: AsyncSession) -> None:
    coach, player, team, game = await seed_module.seed_demo_state(db_session)

    coach.full_name = "Broken Coach"
    coach.is_active = False
    player.is_verified = False
    team.name = "Broken Team"
    team.invite_code = "BROKEN"
    membership = await db_session.scalar(
        select(TeamMembership).where(
            TeamMembership.team_id == team.id,
            TeamMembership.user_id == player.id,
        )
    )
    assert membership is not None
    membership.is_active = False
    membership.jersey_number = 99
    game.status = GameStatus.COMPLETED
    game.location = "Wrong Gym"
    await db_session.flush()

    (
        repaired_coach,
        repaired_player,
        repaired_team,
        repaired_game,
    ) = await seed_module.seed_demo_state(db_session)

    assert repaired_coach.full_name == "Demo Coach"
    assert repaired_coach.is_active is True
    assert repaired_player.is_verified is True
    assert repaired_team.name == seed_module.TEAM_NAME
    assert repaired_team.invite_code == seed_module.TEAM_INVITE_CODE
    repaired_membership = await db_session.scalar(
        select(TeamMembership).where(
            TeamMembership.team_id == repaired_team.id,
            TeamMembership.user_id == repaired_player.id,
        )
    )
    assert repaired_membership is not None
    assert repaired_membership.is_active is True
    assert repaired_membership.jersey_number == 23
    assert repaired_game.status == GameStatus.SCHEDULED
    assert repaired_game.location == "Demo High School Gym"


def test_seed_cli_refuses_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    from nextballup_core.settings import reload_settings

    reload_settings()
    try:
        with pytest.raises(SystemExit) as exc:
            seed_module.main()
        assert exc.value.code == 2
    finally:
        # Restore the test-suite env so follow-on tests don't see production.
        monkeypatch.setenv("APP_ENV", "test")
        reload_settings()


def test_seed_cli_refuses_nonlocal_database_target(monkeypatch: pytest.MonkeyPatch) -> None:
    original_database_url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://nextballup:nextballup_dev@db.example.com:5432/nextballup",
    )
    from nextballup_core.settings import reload_settings

    reload_settings()
    try:
        with pytest.raises(SystemExit) as exc:
            seed_module.main()
        assert exc.value.code == 2
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("DATABASE_URL", original_database_url)
        reload_settings()
