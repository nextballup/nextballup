from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from nextballup_db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserTotpSecret(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-user TOTP enrollment.

    `secret_ciphertext` holds the symmetric-encrypted shared secret. Today the
    cipher is `aes-gcm-pbkdf2` keyed off `MFA_SECRET_KEY`; future hardening
    moves the key custody to KMS without touching the column shape.

    `confirmed_at` is non-null once the user has proven possession of the
    secret by submitting a valid code; until then the row exists but cannot
    satisfy a login challenge. Disabling MFA marks `disabled_at` rather than
    deleting the row so audit trails can show enrollment history.
    """

    __tablename__ = "user_totp_secrets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    secret_ciphertext: Mapped[str] = mapped_column(String(512), nullable=False)
    cipher: Mapped[str] = mapped_column(String(32), nullable=False, default="aes-gcm-pbkdf2")
    issuer_label: Mapped[str] = mapped_column(String(255), nullable=False, default="NextBallUp")
    account_label: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_user_totp_secrets_confirmed", "user_id", "confirmed_at"),)


class MfaRecoveryCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Hashed single-use recovery code for an enrolled user.

    Only a keyed SHA-256 digest of the code is persisted; the plaintext is
    shown to the user exactly once at issuance time. Used codes have
    `used_at` set so replays are rejected.
    """

    __tablename__ = "mfa_recovery_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_mfa_recovery_codes_user_active", "user_id", "used_at"),)
