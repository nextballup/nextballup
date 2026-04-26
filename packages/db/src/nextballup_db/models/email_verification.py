from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nextballup_db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EmailVerificationToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One-time-use email verification token.

    Only the SHA-256 hash of the token is stored. The raw token is delivered
    out-of-band (email link). Confirm-time lookup uses the hash so a DB read
    cannot reveal a valid token.

    Replay protection: `used_at` is set the first time confirm succeeds; any
    subsequent presentation of the same token is rejected. Expiry is bounded
    by `expires_at`.
    """

    __tablename__ = "email_verification_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_ip: Mapped[str | None] = mapped_column(INET)
    requested_user_agent: Mapped[str | None] = mapped_column(String(500))
    confirmed_ip: Mapped[str | None] = mapped_column(INET)

    __table_args__ = (
        Index("ix_email_verification_tokens_user_created", "user_id", "created_at"),
        Index(
            "ix_email_verification_tokens_active",
            "user_id",
            "used_at",
            "expires_at",
        ),
    )
