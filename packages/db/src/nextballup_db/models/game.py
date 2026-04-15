from __future__ import annotations

import uuid
from datetime import date as _date
from datetime import time as _time
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nextballup_core.enums import GameStatus, GameType
from nextballup_db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from nextballup_db.models.team import Team
    from nextballup_db.models.video import Video


class Game(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "games"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    opponent_name: Mapped[str | None] = mapped_column(String(255))
    game_type: Mapped[GameType] = mapped_column(
        Enum(
            GameType,
            name="game_type",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    date: Mapped[_date] = mapped_column(Date, nullable=False)
    time: Mapped[_time | None] = mapped_column(Time)
    location: Mapped[str | None] = mapped_column(String(255))
    is_home: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[GameStatus] = mapped_column(
        Enum(
            GameStatus,
            name="game_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=GameStatus.SCHEDULED,
    )
    periods: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    period_length_minutes: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    score_team: Mapped[int | None] = mapped_column(Integer)
    score_opponent: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(String(2000))
    processing_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    team: Mapped[Team] = relationship(back_populates="games", lazy="selectin")
    videos: Mapped[list[Video]] = relationship(
        back_populates="game",
        lazy="noload",
        cascade="all, delete-orphan",
        foreign_keys="Video.game_id",
    )

    __table_args__ = (
        UniqueConstraint("id", "team_id", name="uq_games_id_team_id"),
        Index("ix_games_team_date", "team_id", "date"),
        Index("ix_games_status", "status"),
    )
