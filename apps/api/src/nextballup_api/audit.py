from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api.request_meta import client_ip
from nextballup_core.settings import get_settings
from nextballup_db.models.audit import AuditLog


async def write_audit(
    session: AsyncSession,
    *,
    action: str,
    request: Request,
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    extra: dict[str, Any] | None = None,
) -> AuditLog:
    """Insert an audit_logs row. Caller controls flush/commit timing.

    PII discipline: never pass passwords, JWT bodies, or unredacted tokens
    in `extra`. Validation errors should pass field names but not values.
    """
    entry = AuditLog(
        action=action,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        resource_type=resource_type,
        resource_id=resource_id,
        team_id=team_id,
        ip_address=client_ip(request, settings=get_settings()),
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        request_id=getattr(request.state, "request_id", None),
        extra=extra,
    )
    session.add(entry)
    await session.flush()
    return entry
