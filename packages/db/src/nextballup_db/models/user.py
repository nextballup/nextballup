from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nextballup_core.enums import UserRole
from nextballup_db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from nextballup_db.models.team import TeamMembership


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    phone: Mapped[str | None] = mapped_column(String(20))
    institution: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    session_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        server_default=text("1"),
    )

    # Player-specific fields (nullable for coaches)
    height_inches: Mapped[int | None] = mapped_column(Integer)
    weight_lbs: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[str | None] = mapped_column(String(10))
    graduation_year: Mapped[int | None] = mapped_column(Integer)
    handedness: Mapped[str | None] = mapped_column(String(10))

    # Consent tracking — surfaced for future COPPA / FERPA / BIPA flows
    biometric_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    parental_consent_on_file: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    date_of_birth_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    team_memberships: Mapped[list[TeamMembership]] = relationship(
        back_populates="user",
        lazy="selectin",
        foreign_keys="TeamMembership.user_id",
    )

    __table_args__ = (
        Index("ix_users_email_lower", func.lower(email), unique=True),
        Index("ix_users_role", "role"),
    )
