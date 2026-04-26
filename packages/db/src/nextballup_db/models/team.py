from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nextballup_core.enums import InstitutionType, Sport, TeamLevel, TeamRole
from nextballup_db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from nextballup_db.models.game import Game
    from nextballup_db.models.user import User


class Team(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sport: Mapped[Sport] = mapped_column(
        Enum(
            Sport,
            name="sport",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=Sport.BASKETBALL,
    )
    level: Mapped[TeamLevel] = mapped_column(
        Enum(
            TeamLevel,
            name="team_level",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    institution: Mapped[str | None] = mapped_column(String(255))
    institution_type: Mapped[InstitutionType] = mapped_column(
        Enum(
            InstitutionType,
            name="institution_type",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=InstitutionType.NONE,
    )
    season: Mapped[str] = mapped_column(String(20), nullable=False)
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(10))
    conference: Mapped[str | None] = mapped_column(String(255))
    invite_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[TeamMembership]] = relationship(
        back_populates="team",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    invites: Mapped[list[TeamInvite]] = relationship(
        back_populates="team",
        lazy="noload",
        cascade="all, delete-orphan",
    )
    games: Mapped[list[Game]] = relationship(
        back_populates="team",
        lazy="noload",
        cascade="all, delete-orphan",
    )
    privacy_consents: Mapped[list[TeamPrivacyConsent]] = relationship(
        "TeamPrivacyConsent",
        back_populates="team",
        lazy="noload",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_teams_sport_level", "sport", "level"),
        Index("ix_teams_season", "season"),
    )


class TeamMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "team_memberships"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_role: Mapped[TeamRole] = mapped_column(
        Enum(
            TeamRole,
            name="team_role",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    jersey_number: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # lazy="selectin" on team so that any code path that loads a membership
    # (including /auth/login → _user_public) also has team.name available
    # without triggering a lazy load outside the async greenlet.
    team: Mapped[Team] = relationship(back_populates="memberships", lazy="selectin")
    user: Mapped[User] = relationship(
        back_populates="team_memberships",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),
        UniqueConstraint("team_id", "jersey_number", name="uq_team_memberships_team_jersey"),
        Index("ix_team_memberships_team", "team_id"),
        Index("ix_team_memberships_user", "user_id"),
    )


class TeamInvite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Coach-issued invite codes with role, expiry, and usage cap.

    Distinct from `Team.invite_code`, which is the team's always-on default
    code. TeamInvite rows let a coach hand out time-bounded, role-specific
    codes (e.g. an assistant_coach invite that expires in 7 days, used once).
    """

    __tablename__ = "team_invites"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    invite_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    role: Mapped[TeamRole] = mapped_column(
        Enum(
            TeamRole,
            name="team_role",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        nullable=False,
        default=TeamRole.PLAYER,
    )
    max_uses: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    team: Mapped[Team] = relationship(back_populates="invites")

    __table_args__ = (
        Index("ix_team_invites_team_active", "team_id", "is_active"),
        Index("ix_team_invites_expires_at", "expires_at"),
    )


class TeamPrivacyConsent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Ledger-backed team consent/rights record for video and CV processing.

    This does not replace a signed legal release; it stores the enforceable
    platform-side pointer that upload and processing code can check before
    accepting sensitive athlete video.
    """

    __tablename__ = "team_privacy_consents"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    consent_source: Mapped[str] = mapped_column(String(64), nullable=False)
    covers_video_uploads: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    covers_cv_processing: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    commercial_ml_training_allowed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    minors_authorized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    athlete_pii_authorized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence_uri: Mapped[str | None] = mapped_column(String(1024))
    evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(2000))

    team: Mapped[Team] = relationship(back_populates="privacy_consents")

    __table_args__ = (
        Index("ix_team_privacy_consents_team", "team_id"),
        Index("ix_team_privacy_consents_team_active", "team_id", "revoked_at", "expires_at"),
    )
