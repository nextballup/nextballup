from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nextballup_db.models.base import Base, UUIDPrimaryKeyMixin


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Append-only audit log for state-changing actions.

    Mutability is enforced at the database level by the prevent_audit_mutation
    trigger applied in the initial migration. PII (passwords, raw tokens) must
    never be written here.
    """

    __tablename__ = "audit_logs"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_email: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(64))
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_audit_logs_action_created_at", "action", "created_at"),
        Index("ix_audit_logs_actor_created_at", "actor_user_id", "created_at"),
        Index("ix_audit_logs_actor_email_created_at", "actor_email", "created_at"),
        Index("ix_audit_logs_team_created_at", "team_id", "created_at"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )
