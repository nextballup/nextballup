from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nextballup_db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RefreshSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Server-side refresh-token session state.

    Only a SHA-256 hash of the JWT `jti` is stored. The raw refresh token stays
    in the httpOnly cookie, and each refresh consumes the current row before
    issuing the next row.
    """

    __tablename__ = "refresh_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    jti_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(64))
    replaced_by_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("refresh_sessions.id", ondelete="SET NULL"),
    )
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        Index("ix_refresh_sessions_user_created", "user_id", "created_at"),
        Index("ix_refresh_sessions_user_active", "user_id", "revoked_at", "expires_at"),
    )
