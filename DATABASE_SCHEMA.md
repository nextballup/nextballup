# NextBallUp Database Schema

## Engine & Session

PostgreSQL 16 with extensions: `uuid-ossp`, `pg_trgm` (text search), `btree_gist` (exclusion constraints).

## Tenant Isolation Baseline

Tenant isolation is a **database-level baseline control**, not an app-layer convenience. The initial Alembic revision must enable PostgreSQL row-level security on every tenant-scoped table. API filters and repository `WHERE team_id = ...` clauses are still required, but they are defense-in-depth only.

```sql
-- Applied in the initial migration, not postponed as a hardening step
ALTER TABLE games ENABLE ROW LEVEL SECURITY;
CREATE POLICY games_tenant_isolation ON games
    USING (team_id = current_setting('app.current_team_id')::uuid);
```

```python
# packages/db/src/nextballup_db/engine.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

DATABASE_URL = "postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"

engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
```

## Base & Mixins

```python
# packages/db/src/nextballup_db/models/base.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
```

## User Model

```python
# packages/db/src/nextballup_db/models/user.py
from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, Enum, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, TimestampMixin

if TYPE_CHECKING:
    from .team import TeamMembership


class UserRole(str, enum.Enum):
    COACH = "coach"
    PLAYER = "player"
    ADMIN = "admin"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    institution: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    session_version: Mapped[int] = mapped_column(default=1, nullable=False)

    # Player-specific fields (nullable for coaches)
    height_inches: Mapped[int | None] = mapped_column(Integer)
    weight_lbs: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[str | None] = mapped_column(String(10))  # PG, SG, SF, PF, C, G, F, UTIL
    graduation_year: Mapped[int | None] = mapped_column(Integer)
    handedness: Mapped[str | None] = mapped_column(String(10))  # right, left, ambidextrous

    # Consent tracking
    biometric_consent: Mapped[bool] = mapped_column(default=False, nullable=False)
    parental_consent_on_file: Mapped[bool] = mapped_column(default=False, nullable=False)
    date_of_birth_verified: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Relationships
    team_memberships: Mapped[list[TeamMembership]] = relationship(
        back_populates="user", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_users_email_lower", func.lower(email), unique=True),
        Index("ix_users_role", "role"),
    )
```

## Team & Membership Models

```python
# packages/db/src/nextballup_db/models/team.py
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Enum, Integer, ForeignKey, UniqueConstraint, DateTime, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, TimestampMixin

if TYPE_CHECKING:
    from .user import User
    from .game import Game


class Sport(str, enum.Enum):
    BASKETBALL = "basketball"
    VOLLEYBALL = "volleyball"


class TeamLevel(str, enum.Enum):
    YOUTH = "youth"
    AAU_CLUB = "aau_club"
    MIDDLE_SCHOOL = "middle_school"
    HIGH_SCHOOL = "high_school"
    JUCO = "juco"
    COLLEGE_D3 = "college_d3"
    COLLEGE_D2 = "college_d2"
    COLLEGE_D1 = "college_d1"
    PROFESSIONAL = "professional"
    INTERNATIONAL = "international"


class InstitutionType(str, enum.Enum):
    NONE = "none"
    K12_SCHOOL = "k12_school"
    COLLEGE = "college"
    CLUB = "club"
    ACADEMY = "academy"
    PROFESSIONAL = "professional"


class TeamRole(str, enum.Enum):
    HEAD_COACH = "head_coach"
    ASSISTANT_COACH = "assistant_coach"
    MANAGER = "manager"
    PLAYER = "player"
    CAPTAIN = "captain"


class Team(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sport: Mapped[Sport] = mapped_column(Enum(Sport), nullable=False, default=Sport.BASKETBALL)
    level: Mapped[TeamLevel] = mapped_column(Enum(TeamLevel), nullable=False)
    institution: Mapped[str | None] = mapped_column(String(255))
    institution_type: Mapped[InstitutionType] = mapped_column(
        Enum(InstitutionType), nullable=False, default=InstitutionType.NONE
    )
    season: Mapped[str] = mapped_column(String(20), nullable=False)  # "2026-2027"
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(10))
    conference: Mapped[str | None] = mapped_column(String(255))
    invite_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Relationships
    memberships: Mapped[list[TeamMembership]] = relationship(
        back_populates="team", lazy="selectin", cascade="all, delete-orphan"
    )
    games: Mapped[list[Game]] = relationship(back_populates="team", lazy="noload")

    __table_args__ = (
        Index("ix_teams_sport_level", "sport", "level"),
        Index("ix_teams_season", "season"),
    )


class TeamMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "team_memberships"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    team_role: Mapped[TeamRole] = mapped_column(Enum(TeamRole), nullable=False)
    jersey_number: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    # Relationships
    team: Mapped[Team] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="team_memberships")

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_user"),
        UniqueConstraint("team_id", "jersey_number", name="uq_team_jersey"),
        Index("ix_membership_team", "team_id"),
        Index("ix_membership_user", "user_id"),
    )


class TeamInvite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "team_invites"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    invite_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    role: Mapped[TeamRole] = mapped_column(Enum(TeamRole), nullable=False, default=TeamRole.PLAYER)
    max_uses: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
```

## Game & Lineup Models

```python
# packages/db/src/nextballup_db/models/game.py
from __future__ import annotations

import enum
import uuid
from datetime import date, time

from sqlalchemy import String, Enum, Integer, ForeignKey, Date, Time, Boolean, Float, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class GameType(str, enum.Enum):
    SCRIMMAGE = "scrimmage"
    PRESEASON = "preseason"
    REGULAR_SEASON = "regular_season"
    TOURNAMENT = "tournament"
    PLAYOFF = "playoff"
    PRACTICE = "practice"
    FILM_EXCHANGE = "film_exchange"  # Opponent film uploaded for scouting (no user_id linkage on opponent players)


class GameStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Game(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "games"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    opponent_name: Mapped[str | None] = mapped_column(String(255))
    game_type: Mapped[GameType] = mapped_column(Enum(GameType), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    time: Mapped[time | None] = mapped_column(Time)
    location: Mapped[str | None] = mapped_column(String(255))
    is_home: Mapped[bool] = mapped_column(default=True, nullable=False)
    status: Mapped[GameStatus] = mapped_column(
        Enum(GameStatus), nullable=False, default=GameStatus.SCHEDULED
    )
    periods: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    period_length_minutes: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    score_team: Mapped[int | None] = mapped_column(Integer)
    score_opponent: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(String(2000))
    processing_metadata: Mapped[dict | None] = mapped_column(JSONB)

    # Relationships
    team: Mapped[Team] = relationship(back_populates="games")
    videos: Mapped[list[Video]] = relationship(back_populates="game", lazy="selectin")
    lineup_entries: Mapped[list[LineupEntry]] = relationship(
        back_populates="game", cascade="all, delete-orphan"
    )
    possessions: Mapped[list[Possession]] = relationship(back_populates="game", lazy="noload")
    events: Mapped[list[Event]] = relationship(back_populates="game", lazy="noload")

    __table_args__ = (
        Index("ix_games_team_date", "team_id", "date"),
        Index("ix_games_status", "status"),
    )


class LineupEntry(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "lineup_entries"

    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    jersey_number: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[str | None] = mapped_column(String(10))
    starter: Mapped[bool] = mapped_column(default=False, nullable=False)
    minutes_played: Mapped[float | None] = mapped_column(Float)

    game: Mapped[Game] = relationship(back_populates="lineup_entries")

    __table_args__ = (
        Index("ix_lineup_game", "game_id"),
    )


class Possession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "possessions"

    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    possession_number: Mapped[int] = mapped_column(Integer, nullable=False)
    offense_team: Mapped[str] = mapped_column(String(10), nullable=False)  # "team" or "opponent"
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    start_game_clock: Mapped[str | None] = mapped_column(String(10))
    end_game_clock: Mapped[str | None] = mapped_column(String(10))
    outcome: Mapped[str | None] = mapped_column(String(50))  # score, miss, turnover, foul, end_of_period
    points_scored: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Score context (critical for tendency analysis — play calling differs by score differential)
    score_differential: Mapped[int | None] = mapped_column(Integer)  # team_score - opponent_score at possession start
    game_context: Mapped[str | None] = mapped_column(String(20))
    # game_context enum: "normal", "clutch" (final 5min reg/OT, diff ≤5), "garbage_time" (final 3min, diff >15)
    
    # Situation type (essential for scouting — ATO sets reveal most rehearsed plays)
    situation_type: Mapped[str | None] = mapped_column(String(30))
    # situation_type enum: "halfcourt", "transition", "ato" (after timeout), "blob" (baseline OOB),
    # "slob" (sideline OOB), "press_break", "free_throw_alignment", "end_of_period"
    
    # Possession conventions (documented here, enforced in events/possession.py):
    # - Offensive rebound = continuation (not new possession), shot clock resets
    # - And-one free throw = part of same possession
    # - Technical free throws = excluded from possessions entirely
    # - End-of-period heaves = tagged end_of_period, excluded from shooting stats by default

    game: Mapped[Game] = relationship(back_populates="possessions")
    events: Mapped[list[Event]] = relationship(back_populates="possession", lazy="selectin")

    __table_args__ = (
        Index("ix_poss_game_period", "game_id", "period"),
    )
```

## Video & Processing Models

```python
# packages/db/src/nextballup_db/models/video.py
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Enum, Integer, Float, ForeignKey, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class VideoStatus(str, enum.Enum):
    PENDING_UPLOAD = "pending_upload"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    TRANSCODING = "transcoding"
    QUEUED = "queued"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class Video(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "videos"

    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key_raw: Mapped[str | None] = mapped_column(String(1024))
    storage_key_mezzanine: Mapped[str | None] = mapped_column(String(1024))
    storage_key_hls: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[VideoStatus] = mapped_column(
        Enum(VideoStatus), nullable=False, default=VideoStatus.PENDING_UPLOAD
    )
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    codec: Mapped[str | None] = mapped_column(String(50))
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    camera_position: Mapped[str | None] = mapped_column(String(50))
    camera_height: Mapped[str | None] = mapped_column(String(50))
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024))

    game: Mapped[Game] = relationship(back_populates="videos")
    processing_jobs: Mapped[list[ProcessingJob]] = relationship(
        back_populates="video", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_videos_game", "game_id"),
        Index("ix_videos_status", "status"),
    )


class ProcessingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "processing_jobs"

    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(50), nullable=False)  # transcode, detection, tracking, etc.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(String(2000))
    result_metadata: Mapped[dict | None] = mapped_column(JSONB)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Worker updates heartbeat every 60s. A scheduled task marks jobs with
    # stale heartbeats (>5 min) as failed with "worker lost" error.
    # Use acks_late=True on Celery tasks so broker redelivers unacked tasks.

    video: Mapped[Video] = relationship(back_populates="processing_jobs")

    __table_args__ = (
        Index("ix_jobs_video_stage", "video_id", "stage"),
    )
```

## Tracking Data Models

```python
# packages/db/src/nextballup_db/models/tracking.py
from __future__ import annotations

import uuid

from sqlalchemy import Float, Integer, ForeignKey, String, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class CourtMapping(UUIDPrimaryKeyMixin, Base):
    """Per-video-segment homography mapping from image → court coordinates."""
    __tablename__ = "court_mappings"

    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    frame_start: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_end: Mapped[int] = mapped_column(Integer, nullable=False)
    homography_matrix: Mapped[list] = mapped_column(ARRAY(Float, dimensions=2), nullable=False)  # 3x3
    reprojection_error: Mapped[float | None] = mapped_column(Float)
    keypoints_detected: Mapped[int | None] = mapped_column(Integer)
    quality_score: Mapped[float | None] = mapped_column(Float)  # 0-1

    __table_args__ = (
        Index("ix_court_video_frames", "video_id", "frame_start"),
    )


class PlayerTrack(UUIDPrimaryKeyMixin, Base):
    """Per-frame player position from tracker output.
    
    CRITICAL: This table must be partitioned by video_id.
    At 30fps × 10 players × 7200s = 2.16M rows per game.
    After 100 games = 216M rows. Without partitioning, queries die.
    
    Partition strategy: RANGE partition on created_date (monthly).
    HASH on UUID was considered but makes cross-partition range queries
    impractical. Monthly RANGE partitions allow efficient time-based
    investigation queries while keeping per-partition size manageable.
    Add composite index on (video_id, frame_number) within each partition.
    
    IMPORTANT: Never query raw tracks from the API. During the Metrics
    stage, compute per-possession summary snapshots (positions at 1s
    intervals, key movements, zone transitions) and serve those from
    the API. Raw tracks are internal to CV pipeline and metrics only.
    """
    __tablename__ = "player_tracks"

    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    track_id: Mapped[int] = mapped_column(Integer, nullable=False)  # Tracker-assigned ID
    player_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")  # Linked after ReID
    )
    frame_number: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    # Image coordinates (bbox)
    bbox_x: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_y: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_w: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_h: Mapped[float] = mapped_column(Float, nullable=False)
    detection_confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Court coordinates (after homography)
    court_x: Mapped[float | None] = mapped_column(Float)
    court_y: Mapped[float | None] = mapped_column(Float)

    # Derived motion
    speed_ft_per_sec: Mapped[float | None] = mapped_column(Float)
    acceleration: Mapped[float | None] = mapped_column(Float)
    direction_degrees: Mapped[float | None] = mapped_column(Float)

    # Identity cues
    team_label: Mapped[str | None] = mapped_column(String(20))  # "team", "opponent", "referee"
    jersey_number_detected: Mapped[int | None] = mapped_column(Integer)
    jersey_confidence: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        Index("ix_ptracks_video_frame", "video_id", "frame_number"),
        Index("ix_ptracks_video_track", "video_id", "track_id"),
        Index("ix_ptracks_player", "player_id"),
    )


class BallTrack(UUIDPrimaryKeyMixin, Base):
    """Per-frame ball position.
    
    Partition by video_id (same strategy as player_tracks).
    216K rows per game (1 ball × 30fps × 7200s).
    """
    __tablename__ = "ball_tracks"

    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    frame_number: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    bbox_x: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_y: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_w: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_h: Mapped[float] = mapped_column(Float, nullable=False)
    detection_confidence: Mapped[float] = mapped_column(Float, nullable=False)

    court_x: Mapped[float | None] = mapped_column(Float)
    court_y: Mapped[float | None] = mapped_column(Float)
    court_z: Mapped[float | None] = mapped_column(Float)  # Height estimate

    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="visible"
    )  # visible, partial_occluded, fully_occluded, interpolated
    possession_holder_track_id: Mapped[int | None] = mapped_column(Integer)
    in_air: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("ix_btracks_video_frame", "video_id", "frame_number"),
    )
```

## Event & Tactical Tag Models

```python
# packages/db/src/nextballup_db/models/event.py
from __future__ import annotations

import enum
import uuid

from sqlalchemy import String, Enum, Integer, Float, ForeignKey, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class EventType(str, enum.Enum):
    SHOT_ATTEMPT = "shot_attempt"
    SHOT_MAKE = "shot_make"
    SHOT_MISS = "shot_miss"
    THREE_POINT_ATTEMPT = "three_point_attempt"
    FREE_THROW = "free_throw"
    REBOUND_OFFENSIVE = "rebound_offensive"
    REBOUND_DEFENSIVE = "rebound_defensive"
    ASSIST = "assist"
    POTENTIAL_ASSIST = "potential_assist"
    HOCKEY_ASSIST = "hockey_assist"
    TURNOVER = "turnover"
    STEAL = "steal"
    BLOCK = "block"
    DEFLECTION = "deflection"
    FOUL = "foul"
    CHARGE_DRAWN = "charge_drawn"
    PASS = "pass"
    DRIBBLE_DRIVE = "dribble_drive"
    SCREEN_SET = "screen_set"
    CUT = "cut"
    CLOSEOUT = "closeout"
    HELP_ROTATION = "help_rotation"
    FAST_BREAK = "fast_break"
    TRANSITION = "transition"
    BOX_OUT = "box_out"
    LOOSE_BALL_RECOVERY = "loose_ball_recovery"


class Event(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "events"

    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    possession_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("possessions.id", ondelete="SET NULL")
    )
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), nullable=False)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    frame_number: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    game_clock: Mapped[str | None] = mapped_column(String(10))

    # Court location
    court_x: Mapped[float | None] = mapped_column(Float)
    court_y: Mapped[float | None] = mapped_column(Float)
    zone: Mapped[str | None] = mapped_column(String(50))  # paint, midrange, three_left_corner, etc.

    # Outcome
    outcome: Mapped[str | None] = mapped_column(String(50))  # make, miss, turnover, foul_drawn
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Quality metrics
    shot_quality: Mapped[float | None] = mapped_column(Float)
    defender_distance_ft: Mapped[float | None] = mapped_column(Float)
    pass_risk: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Human correction
    corrected: Mapped[bool] = mapped_column(default=False, nullable=False)
    correction_note: Mapped[str | None] = mapped_column(String(500))
    corrected_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    # Clip reference
    clip_start_seconds: Mapped[float | None] = mapped_column(Float)
    clip_end_seconds: Mapped[float | None] = mapped_column(Float)

    # Relationships
    game: Mapped[Game] = relationship(back_populates="events")
    possession: Mapped[Possession | None] = relationship(back_populates="events")
    actors: Mapped[list[EventActor]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )
    tactical_tags: Mapped[list[TacticalTag]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_events_game_type", "game_id", "event_type"),
        Index("ix_events_game_period", "game_id", "period"),
        Index("ix_events_timestamp", "video_id", "timestamp_seconds"),
    )


class EventActor(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "event_actors"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    track_id: Mapped[int | None] = mapped_column(Integer)
    jersey_number: Mapped[int | None] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    # shooter, passer, receiver, screener, cutter, rebounder,
    # closest_defender, help_defender, blocked_by

    event: Mapped[Event] = relationship(back_populates="actors")

    __table_args__ = (
        Index("ix_actors_event", "event_id"),
        Index("ix_actors_player", "player_id"),
    )


class TacticalTag(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tactical_tags"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    tag: Mapped[str] = mapped_column(String(100), nullable=False)
    # pick_and_roll, pick_and_pop, dho, spain_pnr, flare_screen,
    # stagger_screen, zone_offense, man_offense, press_break,
    # transition_attack, ato (after timeout), blob (baseline out of bounds)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="auto"
    )  # auto, manual

    event: Mapped[Event] = relationship(back_populates="tactical_tags")

    __table_args__ = (
        Index("ix_ttags_event", "event_id"),
        Index("ix_ttags_tag", "tag"),
    )
```

## Metrics Models

```python
# packages/db/src/nextballup_db/models/metrics.py
from __future__ import annotations

import uuid

from sqlalchemy import String, Float, Integer, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class PlayerGameMetrics(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Aggregated per-player per-game metrics. One row per player per game."""
    __tablename__ = "player_game_metrics"

    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False
    )

    # Box score
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rebounds_off: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rebounds_def: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    assists: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    steals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    turnovers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fouls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    minutes_played: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Shooting
    fga: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fgm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    three_pa: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    three_pm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fta: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ftm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Hidden impact — Spatial IQ
    spatial_iq_composite: Mapped[float | None] = mapped_column(Float)
    spacing_quality: Mapped[float | None] = mapped_column(Float)
    advantage_creation: Mapped[float | None] = mapped_column(Float)
    decision_latency_ms: Mapped[float | None] = mapped_column(Float)
    off_ball_value: Mapped[float | None] = mapped_column(Float)

    # Hidden impact — Movement
    distance_miles: Mapped[float | None] = mapped_column(Float)
    avg_speed_mph: Mapped[float | None] = mapped_column(Float)
    sprint_count: Mapped[int | None] = mapped_column(Integer)
    screens_set: Mapped[int | None] = mapped_column(Integer)
    cuts: Mapped[int | None] = mapped_column(Integer)

    # Hidden impact — Defense
    on_ball_fg_pct_allowed: Mapped[float | None] = mapped_column(Float)
    closeout_speed_avg: Mapped[float | None] = mapped_column(Float)
    help_rotations: Mapped[int | None] = mapped_column(Integer)
    floor_shrink_score: Mapped[float | None] = mapped_column(Float)
    contest_rate: Mapped[float | None] = mapped_column(Float)

    # Tendency snapshot (JSON blob for flexible schema evolution)
    tendency_data: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_pgm_game_player", "game_id", "player_id", unique=True),
        Index("ix_pgm_player_team", "player_id", "team_id"),
    )
```

## Clip & Playlist Models

```python
# packages/db/src/nextballup_db/models/clip.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Float, Integer, ForeignKey, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Clip(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clips"

    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024))
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String(100)))
    event_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))

    __table_args__ = (
        Index("ix_clips_game", "game_id"),
        Index("ix_clips_video", "video_id"),
    )


class Playlist(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "playlists"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))
    clip_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="team")

    __table_args__ = (
        Index("ix_playlists_team", "team_id"),
    )
```

## Note Models (team-scoped collaboration)

```python
# packages/db/src/nextballup_db/models/note.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Float, Boolean, ForeignKey, DateTime, Integer, Index, func
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Note(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Team-scoped annotation on events, clips, games, or possessions.
    
    These are coaching notes, NOT social comments. Visible only to team members.
    Notes involving minors may be held for coach review (pending_review flag).
    
    Retention: notes are discoverable records. Retain for the lifetime of the
    team + 1 year after team deletion. FERPA: if institution_type is k12_school,
    notes about students are education records subject to access/amendment rights.
    """
    __tablename__ = "notes"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # event, clip, game, possession
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    timestamp_seconds: Mapped[float | None] = mapped_column(Float)  # anchor to video timestamp
    is_pinned: Mapped[bool] = mapped_column(default=False, nullable=False)
    pending_review: Mapped[bool] = mapped_column(default=False, nullable=False)
    # True when a player-to-player note involves a minor — held for coach approval
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Soft delete (notes are discoverable records — never hard delete)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # UI hides soft-deleted notes. Admin export includes them for e-discovery.

    mentions: Mapped[list[NoteMention]] = relationship(
        back_populates="note", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_notes_target", "target_type", "target_id"),
        Index("ix_notes_team", "team_id"),
        Index("ix_notes_author", "author_id"),
    )


class NoteMention(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "note_mentions"

    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notes.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    notified: Mapped[bool] = mapped_column(default=False, nullable=False)

    note: Mapped[Note] = relationship(back_populates="mentions")

    __table_args__ = (
        Index("ix_mentions_user", "user_id"),
    )
```

## Alert Models

```python
# packages/db/src/nextballup_db/models/alert.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, ForeignKey, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Alert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alerts"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    delivery: Mapped[list] = mapped_column(JSONB, nullable=False)  # ["in_app", "email"]
    active: Mapped[bool] = mapped_column(default=True, nullable=False)

    __table_args__ = (
        Index("ix_alerts_team", "team_id"),
    )


class AlertTriggered(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "alerts_triggered"

    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id"), nullable=False
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id")
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload: Mapped[dict | None] = mapped_column(JSONB)
    acknowledged: Mapped[bool] = mapped_column(default=False, nullable=False)
```

## Migration Strategy

Use Alembic with async support. All migrations must be reversible (implement both `upgrade()` and `downgrade()`). In this scaffold, the commands below describe the target migration workflow once the `alembic/` directory exists.

```bash
# Generate migration from model changes
uv run alembic revision --autogenerate -m "add_spatial_iq_fields"

# Apply
uv run alembic upgrade head

# Rollback one
uv run alembic downgrade -1
```

Naming convention for constraints (set in alembic env.py):

```python
convention = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
```
