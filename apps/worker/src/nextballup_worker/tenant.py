from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.enums import UserRole

# Synthetic operator identity used by worker-owned audit rows. Workers are not
# logged-in users; the audit trail needs a stable actor_email so SOC 2-minded
# reviewers can distinguish system actions from user actions.
WORKER_ACTOR_EMAIL = "worker@nextballup.internal"


async def set_worker_operator_role(session: AsyncSession) -> None:
    """Bind only the `app.current_user_role = admin` GUC.

    This is the minimum context required to read worker-owned rows before we
    know which tenant they belong to — the admin fallback in each SELECT policy
    (migration 0005) admits the lookup so we can discover `team_id` and bind
    the full tenant context next.
    """
    await session.execute(
        text("SELECT set_config('app.current_user_role', :role, true)").bindparams(
            role=UserRole.ADMIN.value
        )
    )


async def set_worker_context(session: AsyncSession, *, team_id: uuid.UUID) -> None:
    """Bind the full tenant + admin-role GUCs.

    Worker INSERTs/UPDATEs on team-scoped tables are gated by the
    `team_id = current_team_id` WITH CHECK clauses, so setting the tenant
    GUC to the row's owning team is how we pass the write policies; the
    admin-role GUC keeps SELECT open for joined lookups across tables.

    The worker uses a per-task engine with NullPool. Any helper that commits
    may close the current connection, so callers must bind this context again
    before the next RLS-protected statement.
    """
    await set_worker_operator_role(session)
    await session.execute(
        text("SELECT set_config('app.current_team_id', :team_id, true)").bindparams(
            team_id=str(team_id)
        )
    )


async def clear_worker_context(session: AsyncSession) -> None:
    await session.execute(text("SELECT set_config('app.current_team_id', '', true)"))
    await session.execute(text("SELECT set_config('app.current_user_role', '', true)"))
