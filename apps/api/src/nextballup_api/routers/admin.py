"""Operator-only endpoints (platform admin).

These are the surfaces where a platform admin — not a team coach — needs to
see across tenants. The primary use case today is compliance/incident
review via the audit log: SOC 2 evidence, GDPR subject-access timelines, and
post-incident forensics all require the ability to scan who did what across
the entire deployment. Coach roles MUST NOT have access — their view is
already tenant-scoped through teams/games/videos.

Audit log rows are immutable at the database level (see the
prevent_audit_mutation trigger installed by the initial migration), so this
router is strictly read-only and never mutates state.
"""

from __future__ import annotations

import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api.audit import write_audit
from nextballup_api.deps import get_current_user, get_db
from nextballup_api.permissions import require_user_role
from nextballup_core.constants import AuditAction
from nextballup_core.enums import UserRole
from nextballup_core.errors import ValidationFailedError
from nextballup_core.schemas.admin import AuditLogEntry, AuditLogPage
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])

_MAX_PAGE_SIZE = 200
_DEFAULT_PAGE_SIZE = 50


def _encode_cursor(row: AuditLog) -> str:
    """Opaque cursor = (created_at_iso, row_id). Stable under inserts because
    we order by (created_at DESC, id DESC) and filter strictly below the
    cursor's (created_at, id) tuple."""
    raw = f"{row.created_at.isoformat()}|{row.id}"
    return urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        ts_str, id_str = raw.split("|", 1)
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts, uuid.UUID(id_str)
    except (ValueError, UnicodeDecodeError, Exception) as exc:
        raise ValidationFailedError("Invalid audit log cursor", details={"cursor": cursor}) from exc


@router.get("/audit/logs", response_model=AuditLogPage)
async def list_audit_logs(
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    team_id: uuid.UUID | None = Query(default=None),
    actor_user_id: uuid.UUID | None = Query(default=None),
    actor_email: str | None = Query(default=None, max_length=255),
    action: str | None = Query(
        default=None,
        max_length=80,
        description="Exact match against the dot-namespaced action identifier.",
    ),
    resource_type: str | None = Query(default=None, max_length=40),
    resource_id: uuid.UUID | None = Query(default=None),
    from_ts: datetime | None = Query(
        default=None,
        description="Inclusive lower bound on created_at (ISO 8601).",
    ),
    to_ts: datetime | None = Query(
        default=None,
        description="Exclusive upper bound on created_at (ISO 8601).",
    ),
    limit: int = Query(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None),
) -> AuditLogPage:
    """Paginated view over the append-only audit log.

    Ordered by (created_at DESC, id DESC) so "latest activity first" is
    stable even when two rows share the same timestamp. Admin-only: tenant
    coaches already have tenant-scoped visibility through the normal
    resource endpoints; this view is for cross-tenant incident review.
    """
    require_user_role(current_user, UserRole.ADMIN)

    filters = []
    if team_id is not None:
        filters.append(AuditLog.team_id == team_id)
    if actor_user_id is not None:
        filters.append(AuditLog.actor_user_id == actor_user_id)
    if actor_email is not None:
        filters.append(AuditLog.actor_email == actor_email)
    if action is not None:
        filters.append(AuditLog.action == action)
    if resource_type is not None:
        filters.append(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        filters.append(AuditLog.resource_id == resource_id)
    if from_ts is not None:
        if from_ts.tzinfo is None:
            from_ts = from_ts.replace(tzinfo=UTC)
        filters.append(AuditLog.created_at >= from_ts)
    if to_ts is not None:
        if to_ts.tzinfo is None:
            to_ts = to_ts.replace(tzinfo=UTC)
        filters.append(AuditLog.created_at < to_ts)

    if cursor is not None:
        cursor_ts, cursor_id = _decode_cursor(cursor)
        # Strict tuple-less-than so we never return the cursor row itself
        # and we survive ties on created_at.
        filters.append(
            (AuditLog.created_at < cursor_ts)
            | and_(AuditLog.created_at == cursor_ts, AuditLog.id < cursor_id)
        )

    # +1 so we can tell whether there is another page without a second query.
    stmt = (
        select(AuditLog)
        .where(*filters)
        .order_by(desc(AuditLog.created_at), desc(AuditLog.id))
        .limit(limit + 1)
    )
    rows = list((await session.execute(stmt)).scalars())
    next_cursor: str | None = None
    if len(rows) > limit:
        next_cursor = _encode_cursor(rows[limit - 1])
        rows = rows[:limit]
    page = AuditLogPage(
        items=[AuditLogEntry.model_validate(r) for r in rows],
        next_cursor=next_cursor,
    )
    await write_audit(
        session,
        action=AuditAction.ADMIN_AUDIT_LOGS_VIEWED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="audit_log",
        extra={
            "filters": {
                "team_id": str(team_id) if team_id is not None else None,
                "actor_user_id": str(actor_user_id) if actor_user_id is not None else None,
                "actor_email": actor_email,
                "action": action,
                "resource_type": resource_type,
                "resource_id": str(resource_id) if resource_id is not None else None,
                "from_ts": from_ts.isoformat() if from_ts is not None else None,
                "to_ts": to_ts.isoformat() if to_ts is not None else None,
                "limit": limit,
                "cursor": cursor,
            },
            "result_count": len(page.items),
            "has_next_page": page.next_cursor is not None,
        },
    )
    await session.commit()
    return page
