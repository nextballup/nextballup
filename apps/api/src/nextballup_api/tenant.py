from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.enums import UserRole


async def set_user_context(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Bind the authenticated user for RLS policies that need self-membership access."""
    await session.execute(
        text("SELECT set_config('app.current_user_id', :user_id, true)").bindparams(
            user_id=str(user_id)
        )
    )


async def set_user_role_context(session: AsyncSession, role: UserRole | str) -> None:
    await session.execute(
        text("SELECT set_config('app.current_user_role', :role, true)").bindparams(role=str(role))
    )


async def set_include_deleted_context(session: AsyncSession, include_deleted: bool) -> None:
    value = "true" if include_deleted else ""
    await session.execute(
        text("SELECT set_config('app.include_deleted', :value, true)").bindparams(value=value)
    )


async def set_join_invite_context(session: AsyncSession, invite_code: str) -> None:
    await session.execute(
        text("SELECT set_config('app.current_join_invite_code', :invite_code, true)").bindparams(
            invite_code=invite_code
        )
    )


async def set_tenant_context(session: AsyncSession, team_id: uuid.UUID) -> None:
    """Bind the per-transaction `app.current_team_id` GUC for RLS.

    Tenant-scoped tables (teams, team_memberships, team_invites, audit_logs)
    have policies of the form
        USING (team_id = current_setting('app.current_team_id', true)::uuid)
    which fail closed (return NULL → zero rows) when the GUC is unset. Calling
    this before issuing tenant-scoped queries is the wiring that makes RLS
    actually filter once the app connects as a non-owner DB role in production.

    Uses set_config(..., is_local=true) — equivalent to SET LOCAL but accepts
    bound parameters, so we never interpolate UUIDs into raw SQL.
    """
    await session.execute(
        text("SELECT set_config('app.current_team_id', :team_id, true)").bindparams(
            team_id=str(team_id)
        )
    )


async def clear_tenant_context(session: AsyncSession) -> None:
    """Reset the GUC mid-transaction. SET LOCAL would also auto-clear at COMMIT,
    so this is only needed when a single transaction crosses tenants (e.g. the
    join-by-code lookup before membership is established)."""
    await session.execute(text("SELECT set_config('app.current_team_id', '', true)"))


async def clear_join_invite_context(session: AsyncSession) -> None:
    await session.execute(text("SELECT set_config('app.current_join_invite_code', '', true)"))
