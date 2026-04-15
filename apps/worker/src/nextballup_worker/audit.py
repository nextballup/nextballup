from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_db.models.audit import AuditLog
from nextballup_worker.tenant import WORKER_ACTOR_EMAIL


async def write_worker_audit(
    session: AsyncSession,
    *,
    action: str,
    team_id: uuid.UUID | None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> AuditLog:
    """Write an audit row for a worker-initiated action.

    Workers have no logged-in user, so `actor_user_id` stays NULL and
    `actor_email` is the synthetic operator address. `request_id` is optional
    and is set to the Celery task_id (or a dispatcher correlation id) so the
    whole lifecycle can be traced.
    """
    entry = AuditLog(
        action=action,
        actor_user_id=None,
        actor_email=WORKER_ACTOR_EMAIL,
        resource_type=resource_type,
        resource_id=resource_id,
        team_id=team_id,
        ip_address=None,
        user_agent="nextballup-worker",
        request_id=request_id,
        extra=extra,
    )
    session.add(entry)
    await session.flush()
    return entry
