from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditLogEntry(BaseModel):
    """Serialized append-only audit record returned to platform operators."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    action: str
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    resource_type: str | None
    resource_id: uuid.UUID | None
    team_id: uuid.UUID | None
    ip_address: str | None
    user_agent: str | None
    request_id: str | None
    extra: dict[str, Any] | None


class AuditLogPage(BaseModel):
    """Cursor-paginated audit log response.

    `next_cursor` is an opaque ISO8601 timestamp — the last row's `created_at`
    minus a unique suffix. Operators page forward by echoing it back.
    """

    items: list[AuditLogEntry]
    next_cursor: str | None = Field(
        default=None,
        description="Pass as ?cursor= on the next call, or null when there are no more rows.",
    )
