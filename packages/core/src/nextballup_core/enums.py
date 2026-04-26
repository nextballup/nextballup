from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    COACH = "coach"
    PLAYER = "player"
    ADMIN = "admin"


class Sport(StrEnum):
    BASKETBALL = "basketball"
    VOLLEYBALL = "volleyball"


class TeamLevel(StrEnum):
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


class InstitutionType(StrEnum):
    NONE = "none"
    K12_SCHOOL = "k12_school"
    COLLEGE = "college"
    CLUB = "club"
    ACADEMY = "academy"
    PROFESSIONAL = "professional"


class TeamRole(StrEnum):
    HEAD_COACH = "head_coach"
    ASSISTANT_COACH = "assistant_coach"
    MANAGER = "manager"
    PLAYER = "player"
    CAPTAIN = "captain"


class GameType(StrEnum):
    SCRIMMAGE = "scrimmage"
    PRESEASON = "preseason"
    REGULAR_SEASON = "regular_season"
    TOURNAMENT = "tournament"
    PLAYOFF = "playoff"
    PRACTICE = "practice"
    FILM_EXCHANGE = "film_exchange"


class GameStatus(StrEnum):
    SCHEDULED = "scheduled"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class CameraPosition(StrEnum):
    SIDELINE = "sideline"
    BASELINE = "baseline"
    ELEVATED_CORNER = "elevated_corner"
    BROADCAST = "broadcast"
    OTHER = "other"


class CameraHeight(StrEnum):
    FLOOR = "floor"
    ELEVATED = "elevated"
    OVERHEAD = "overhead"


class VideoStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    TRANSCODING = "transcoding"
    QUEUED = "queued"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class ProcessingJobStage(StrEnum):
    """Pipeline stages emitted by future workers. Phase 3 only ever creates
    `transcode` placeholders; the rest are reserved for downstream phases."""

    TRANSCODE = "transcode"
    DETECTION = "detection"
    TRACKING = "tracking"
    COURT_MAPPING = "court_mapping"
    EVENTS = "events"
    METRICS = "metrics"


class ProcessingJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class UploadMethod(StrEnum):
    PUT = "PUT"
    MULTIPART = "MULTIPART"


class ModelArtifactStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    RETIRED = "retired"
    BLOCKED = "blocked"


class VideoEventType(StrEnum):
    SHOT_ATTEMPT = "shot_attempt"
    SHOT_MADE = "shot_made"
    REBOUND = "rebound"
    PASS = "pass"


class ReviewStatus(StrEnum):
    MACHINE_ONLY = "machine_only"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class BillingAccountStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class SubscriptionStatus(StrEnum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"


class PlanCode(StrEnum):
    """Stable plan identifiers seeded by migration 0012.

    The integer tier on the row drives capability comparisons; the code is
    used by humans and seed scripts. Adding a new plan adds a value here and
    a row in the seed migration.
    """

    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"
