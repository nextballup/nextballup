# NextBallUp

## Quick Facts

- **What**: AI vision platform for basketball player analysis — "hidden impact" metrics beyond box scores
- **Stack**: Python 3.12+ (uv workspaces) · FastAPI · PostgreSQL 16 · Redis · Next.js 15 · Celery
- **CV Pipeline**: RF-DETR → BoT-SORT (via BoxMOT) → ViTPose++ → SmolVLM2 (jersey OCR) → court homography → event classifiers
- **License constraint**: ALL dependencies must be Apache-2.0, MIT, or BSD. No AGPL. No "non-commercial" licenses. No Ultralytics YOLO. No OpenPose. No AlphaPose.
- **Target**: Basketball first. Volleyball expansion planned but not in MVP scope.
- **User roles**: Players and Coaches create accounts. Coaches create and manage Teams. Players join Teams via invite.

## Commands

```bash
# Setup
uv sync                          # Install all workspace dependencies
docker compose up -d             # Start PostgreSQL, Redis, MinIO
uv run alembic upgrade head      # Run database migrations

# Development
uv run fastapi dev apps/api/main.py    # API server (port 8000)
cd apps/web && pnpm dev                # Frontend (port 3000)
uv run celery -A apps/worker.celery_app worker --loglevel=info  # Worker

# Testing
uv run pytest                          # All tests
uv run pytest tests/unit               # Unit only
uv run pytest tests/integration        # Integration (needs docker)
uv run pytest tests/cv --slow          # CV pipeline tests (GPU)

# Code quality
uv run ruff check .                    # Lint
uv run ruff format .                   # Format
uv run mypy packages/                  # Type check
```

## Architecture

```
nextballup/
├── CLAUDE.md                    # This file
├── pyproject.toml               # Root workspace config (uv)
├── docker-compose.yml           # Local dev services
├── alembic/                     # Database migrations
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
├── packages/                    # Shared Python packages
│   ├── core/                    # Domain models, enums, constants
│   │   ├── pyproject.toml
│   │   └── src/nextballup_core/
│   │       ├── models/          # Pydantic schemas (API contracts)
│   │       │   ├── user.py      # UserCreate, UserRead, UserRole enum
│   │       │   ├── team.py      # TeamCreate, TeamRead, TeamMembership
│   │       │   ├── game.py      # GameCreate, GameRead, GameStatus enum
│   │       │   ├── video.py     # VideoUpload, VideoStatus, ProcessingJob
│   │       │   ├── event.py     # GameEvent, EventType enum, PossessionEvent
│   │       │   ├── player_profile.py  # PlayerProfile, TendencyCard, ShootingProfile
│   │       │   └── metrics.py   # SpatialIQ, ConversionRate, PredictiveFeature
│   │       ├── enums.py         # Shared enums (Sport, UserRole, etc.)
│   │       └── constants.py     # Court dimensions, config defaults
│   ├── db/                      # Database layer (SQLAlchemy + Alembic)
│   │   ├── pyproject.toml
│   │   └── src/nextballup_db/
│   │       ├── engine.py        # Engine/session factory
│   │       ├── models/          # ORM models (1:1 with tables)
│   │       │   ├── base.py      # Base, mixins (TimestampMixin, TenantMixin)
│   │       │   ├── user.py      # User, UserRole
│   │       │   ├── team.py      # Team, TeamMembership, TeamInvite
│   │       │   ├── roster.py    # Roster, RosterEntry
│   │       │   ├── game.py      # Game, Period, Possession
│   │       │   ├── video.py     # Video, VideoSegment, ProcessingJob
│   │       │   ├── tracking.py  # PlayerTrack, BallTrack, CourtMapping
│   │       │   ├── event.py     # Event, EventActor, TacticalTag
│   │       │   ├── metrics.py   # MetricDefinition, MetricSeries, PlayerMetricSnapshot
│   │       │   └── clip.py      # Clip, Playlist, ClipTag
│   │       └── repositories/    # Data access patterns (one per aggregate)
│   │           ├── user_repo.py
│   │           ├── team_repo.py
│   │           ├── game_repo.py
│   │           └── video_repo.py
│   ├── cv_pipeline/             # Computer vision pipeline
│   │   ├── pyproject.toml
│   │   └── src/nextballup_cv/
│   │       ├── pipeline.py      # Orchestrator: video → structured data
│   │       ├── detection/       # RF-DETR player/ball/hoop detector
│   │       │   ├── detector.py
│   │       │   └── config.py
│   │       ├── tracking/        # BoT-SORT via BoxMOT
│   │       │   ├── tracker.py
│   │       │   └── reid.py      # Team color + jersey ReID
│   │       ├── court/           # Court registration + homography
│   │       │   ├── keypoints.py
│   │       │   ├── homography.py
│   │       │   └── court_model.py  # Canonical court coordinate system
│   │       ├── pose/            # ViTPose++ wrapper
│   │       │   ├── estimator.py
│   │       │   └── features.py  # Pose-derived features (speed, acceleration)
│   │       ├── ocr/             # SmolVLM2 jersey number + scoreboard
│   │       │   ├── jersey.py
│   │       │   └── scoreboard.py
│   │       ├── events/          # Event detection (heuristic + ML)
│   │       │   ├── detector.py  # Shot, pass, turnover, rebound detection
│   │       │   ├── possession.py # Possession segmentation
│   │       │   └── tactical.py  # P&R, DHO, flare, zone/man classification
│   │       └── metrics/         # Derived metric computation
│   │           ├── conversion_rates.py
│   │           ├── spatial_iq.py
│   │           └── predictive.py  # Shot quality, pass risk
│   └── clip_engine/             # Video clip generation
│       ├── pyproject.toml
│       └── src/nextballup_clips/
│           ├── cutter.py        # FFmpeg-based clip extraction
│           ├── playlist.py      # Playlist assembly
│           └── export.py        # MP4/HLS export
├── apps/                        # Deployable applications
│   ├── api/                     # FastAPI application
│   │   ├── pyproject.toml
│   │   ├── main.py              # App factory, middleware, lifespan
│   │   ├── dependencies.py      # Dependency injection (db sessions, auth)
│   │   ├── auth/                # Authentication (JWT + OAuth2)
│   │   │   ├── router.py        # /auth/register, /auth/login, /auth/refresh
│   │   │   ├── service.py       # Password hashing, token creation
│   │   │   └── permissions.py   # Role-based access (coach/player/admin)
│   │   ├── routers/             # API route modules
│   │   │   ├── users.py         # /users
│   │   │   ├── teams.py         # /teams
│   │   │   ├── games.py         # /games
│   │   │   ├── videos.py        # /videos (upload, status, playback)
│   │   │   ├── events.py        # /events (auto-tagged events)
│   │   │   ├── players.py       # /players (profiles, tendencies)
│   │   │   ├── metrics.py       # /metrics (spatial IQ, conversion rates)
│   │   │   ├── clips.py         # /clips (generate, playlists)
│   │   │   ├── scouting.py      # /scouting (reports, comparisons)
│   │   │   └── search.py        # /search (cross-entity search)
│   │   └── websockets/          # Real-time processing status
│   │       └── processing.py
│   ├── worker/                  # Celery worker (GPU tasks)
│   │   ├── pyproject.toml
│   │   ├── celery_app.py
│   │   └── tasks/
│   │       ├── video_ingest.py  # Transcode, segment, store
│   │       ├── cv_process.py    # Run CV pipeline on video
│   │       ├── clip_generate.py # Generate clips from events
│   │       ├── metrics_compute.py  # Compute derived metrics
│   │       └── report_generate.py  # PDF/slide scouting reports
│   └── web/                     # Next.js 15 frontend
│       ├── package.json
│       ├── next.config.ts
│       ├── tailwind.config.ts
│       └── src/
│           ├── app/             # App router pages
│           ├── components/      # React components
│           ├── stores/          # Zustand state stores
│           ├── hooks/           # Custom hooks
│           └── lib/             # API client, utils
├── training/                    # ML training pipelines (not deployed)
│   ├── detection/               # RF-DETR fine-tuning
│   ├── tracking/                # ReID model training
│   ├── events/                  # Event classifier training
│   └── configs/                 # Hyperparameter configs
├── labeling/                    # Annotation tooling configs
│   ├── cvat/                    # CVAT project templates
│   └── schemas/                 # Label format definitions
├── infra/                       # Infrastructure as code
│   ├── terraform/               # Cloud resources
│   ├── k8s/                     # Kubernetes manifests (later)
│   └── scripts/                 # Deployment scripts
└── tests/
    ├── unit/                    # Fast, no external deps
    ├── integration/             # Needs docker services
    ├── cv/                      # CV pipeline tests (may need GPU)
    └── fixtures/                # Test data, sample frames
```

## Code Style

- **Python**: Ruff for linting and formatting. Line length 100. Google-style docstrings.
- **Type hints**: Required on all function signatures. Use `from __future__ import annotations`.
- **Imports**: `from __future__ import annotations` at top of every Python file. Absolute imports only within packages. Relative imports prohibited.
- **Pydantic**: v2 models for all API request/response schemas. Use `model_validator` not `validator`.
- **SQLAlchemy**: 2.0 style (mapped_column, DeclarativeBase). No legacy Query API.
- **Async**: FastAPI routes are async. Database operations use async sessions. Celery tasks are sync (CPU/GPU bound).
- **Error handling**: Custom exception hierarchy in `packages/core/exceptions.py`. FastAPI exception handlers map to HTTP status codes. Never expose internal errors to users.
- **Testing**: pytest. Use factories (factory_boy) for test data. Fixtures for database sessions. No mocking of database — use test database.
- **Frontend**: TypeScript strict mode. Functional components only. Zustand for state. TanStack Query for server state. No `any` types.

## Constraints

- **License**: Apache-2.0 / MIT / BSD only. Before adding ANY dependency, verify its license. AGPL is a hard block.
- **Privacy**: Player data is tenant-isolated. No cross-team data leakage. Video access requires team membership. Biometric data (pose keypoints, face geometry) requires explicit consent flag before storage. COPPA: age-gate accounts; under-13 requires parental consent workflow. FERPA: if team.institution_type == "k12_school", restrict data export/sharing.
- **Security**: All passwords via bcrypt (rounds=12). JWTs with RS256, 15-minute access tokens, 7-day refresh tokens. All video URLs are signed with expiration (1 hour default). Rate limiting on auth endpoints (5 attempts/minute).
- **Multi-tenancy**: Team is the tenant boundary. All database queries on tenant-scoped data MUST filter by team_id. Use SQLAlchemy events or middleware to enforce.
- **Video**: Accept MP4, MOV, MKV. Max upload 10GB. Transcode to H.264 mezzanine (1080p, 30fps baseline). Store originals in cold tier after 30 days. Generate HLS segments for playback.
- **Court coordinates**: All spatial metrics use NBA court coordinate system (94ft × 50ft, origin at basket center, x along baseline, y along sideline). Map non-NBA courts proportionally.

## Security & SOC 2 Foundations

This section specifies security infrastructure that must be present from day one. These components are expensive to retrofit and are prerequisites for SOC 2 Type II certification (targeted at Scale phase, month 6-12).

### Audit Log — append-only, never delete

Every state-changing operation and every sensitive data access must be recorded in an immutable audit log. This is the single most important SOC 2 control.

```python
# packages/db/src/nextballup_db/models/audit.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, Text, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AuditLog(Base):
    """Append-only audit log. NEVER update or delete rows in this table.
    
    This table supports SOC 2 Trust Service Criteria:
    - CC6.1 (Logical access security)
    - CC7.2 (System monitoring)
    - CC8.1 (Change management)
    """
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    
    # WHO
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # null for system/anonymous
    actor_email: Mapped[str | None] = mapped_column(String(255))  # denormalized for log durability
    actor_role: Mapped[str | None] = mapped_column(String(20))     # coach, player, admin, system
    actor_ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    
    # WHAT
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # Action taxonomy (use these exact strings):
    #   auth.register, auth.login, auth.login_failed, auth.logout, auth.token_refresh
    #   auth.password_change, auth.password_reset
    #   user.update, user.delete, user.consent_granted, user.consent_revoked
    #   team.create, team.update, team.delete, team.member_add, team.member_remove
    #   team.invite_create, team.invite_use
    #   game.create, game.update, game.delete, game.lineup_set
    #   video.upload_init, video.upload_complete, video.process_start, video.process_complete
    #   video.process_fail, video.delete, video.access
    #   event.correct, event.delete
    #   clip.create, clip.share, clip.delete
    #   report.generate, report.download
    #   alert.create, alert.update, alert.delete
    #   player.profile_view (sensitive: tracks who views whose data)
    #   data.export, data.delete_request
    #   admin.impersonate, admin.config_change
    
    # WHERE (resource context)
    resource_type: Mapped[str | None] = mapped_column(String(50))   # user, team, game, video, event, clip, report
    resource_id: Mapped[str | None] = mapped_column(String(36))     # UUID as string
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # tenant context
    
    # DETAILS
    detail: Mapped[dict | None] = mapped_column(JSONB)
    # Examples:
    #   auth.login_failed: {"reason": "invalid_password", "attempt_number": 3}
    #   event.correct: {"field": "event_type", "old_value": "shot_miss", "new_value": "shot_make"}
    #   video.access: {"access_type": "playback", "video_id": "..."}
    #   data.export: {"format": "csv", "row_count": 342, "entity": "events"}
    
    # OUTCOME
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="success"
    )  # success, failure, denied
    
    # Correlation
    request_id: Mapped[str | None] = mapped_column(String(36))  # ties to structured log trace

    __table_args__ = (
        Index("ix_audit_actor_time", "actor_id", "timestamp"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
        Index("ix_audit_team_time", "team_id", "timestamp"),
        Index("ix_audit_action_time", "action", "timestamp"),
    )
```

**Critical rules for audit logging:**
- The `audit_logs` table must NEVER have UPDATE or DELETE operations. Set PostgreSQL row-level security or a trigger to enforce this: `CREATE RULE audit_no_update AS ON UPDATE TO audit_logs DO INSTEAD NOTHING;` and `CREATE RULE audit_no_delete AS ON DELETE TO audit_logs DO INSTEAD NOTHING;`
- Audit writes MUST NOT block the request — use a background task or async write. If the audit write fails, log the failure but do not fail the user request.
- Audit log retention: minimum 1 year for SOC 2, recommend 3 years. Partition by month for query performance: `CREATE TABLE audit_logs (...) PARTITION BY RANGE (timestamp);`
- Every API router must import and call the audit logger. This is not optional.

### Audit Logging Middleware

```python
# apps/api/middleware/audit.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from nextballup_db.models.audit import AuditLog


class AuditContext:
    """Carries audit metadata through a request lifecycle.
    
    Attached to request.state by middleware. Routers call
    request.state.audit.record() to emit audit entries.
    """
    
    def __init__(self, request: Request, actor_id: uuid.UUID | None = None):
        self.request_id = str(uuid.uuid4())
        self.actor_id = actor_id
        self.actor_email: str | None = None
        self.actor_role: str | None = None
        self.actor_ip = request.client.host if request.client else None
        self.user_agent = request.headers.get("user-agent", "")[:512]
        self._entries: list[dict] = []
    
    def record(
        self,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        team_id: uuid.UUID | None = None,
        detail: dict | None = None,
        status: str = "success",
    ) -> None:
        """Queue an audit entry. Flushed after response."""
        self._entries.append({
            "timestamp": datetime.now(timezone.utc),
            "actor_id": self.actor_id,
            "actor_email": self.actor_email,
            "actor_role": self.actor_role,
            "actor_ip": self.actor_ip,
            "user_agent": self.user_agent,
            "action": action,
            "resource_type": resource_type,
            "resource_id": str(resource_id) if resource_id else None,
            "team_id": team_id,
            "detail": detail,
            "status": status,
            "request_id": self.request_id,
        })
    
    @property
    def entries(self) -> list[dict]:
        return self._entries


# Usage in a router:
# 
# @router.post("/teams")
# async def create_team(request: Request, body: TeamCreate, db: AsyncSession = Depends(get_db)):
#     team = await team_repo.create(db, body)
#     request.state.audit.record(
#         action="team.create",
#         resource_type="team",
#         resource_id=team.id,
#         detail={"name": team.name, "sport": team.sport},
#     )
#     return team
```

**Audit log entries required per route (minimum set):**

| Route group | Actions to log |
|---|---|
| Auth | `auth.register`, `auth.login`, `auth.login_failed`, `auth.logout`, `auth.token_refresh` |
| Users | `user.update`, `user.consent_granted`, `user.consent_revoked` |
| Teams | `team.create`, `team.update`, `team.member_add`, `team.member_remove`, `team.invite_create`, `team.invite_use` |
| Games | `game.create`, `game.update`, `game.delete`, `game.lineup_set` |
| Videos | `video.upload_init`, `video.upload_complete`, `video.process_start`, `video.process_complete`, `video.process_fail`, `video.access` |
| Events | `event.correct` |
| Clips | `clip.create`, `clip.share` |
| Scouting | `report.generate`, `report.download` |
| Players | `player.profile_view` (log who views whose profile — privacy audit trail) |
| Data | `data.export` |

### Structured Logging

All application logs must be structured JSON, not free-text. This enables SIEM ingestion, alerting, and SOC 2 evidence collection.

```python
# apps/api/logging_config.py
import logging
import json
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for SOC 2 evidence collection."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add request context if available
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id
        if hasattr(record, "actor_id"):
            log_entry["actor_id"] = str(record.actor_id)
        if hasattr(record, "team_id"):
            log_entry["team_id"] = str(record.team_id)
            
        # Add error context
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
            
        return json.dumps(log_entry)


# Configure in main.py lifespan:
# 
# LOGGING_CONFIG = {
#     "version": 1,
#     "disable_existing_loggers": False,
#     "formatters": {
#         "json": {"()": "apps.api.logging_config.JSONFormatter"},
#     },
#     "handlers": {
#         "console": {
#             "class": "logging.StreamHandler",
#             "stream": "ext://sys.stdout",
#             "formatter": "json",
#         },
#     },
#     "root": {"level": "INFO", "handlers": ["console"]},
#     "loggers": {
#         "nextballup": {"level": "DEBUG"},
#         "uvicorn": {"level": "INFO"},
#         "sqlalchemy.engine": {"level": "WARNING"},
#     },
# }
```

**Logging rules:**
- Never log passwords, tokens, API keys, or full video URLs in plaintext.
- Always include `request_id` in log context for trace correlation.
- Log at ERROR level: unhandled exceptions, processing failures, auth failures after 3 attempts.
- Log at WARNING level: rate limit hits, slow queries (>1s), unusual access patterns.
- Log at INFO level: request start/end, processing stage transitions, audit events.
- Log at DEBUG level: SQL queries (dev only), CV pipeline step timings, detection counts.

### Request ID Middleware

Every request gets a unique ID propagated through logs, audit entries, and error responses.

```python
# apps/api/middleware/request_id.py
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

### Encryption

**In transit:** TLS 1.2+ required on all endpoints. In development, the FastAPI dev server runs plain HTTP — TLS is terminated at the load balancer in production. Never send tokens, passwords, or PII over non-TLS connections.

**At rest — database:**
```sql
-- PostgreSQL 16 with encryption at rest
-- For AWS RDS: enable storage encryption at instance creation (AES-256, AWS KMS)
-- For self-hosted: use LUKS/dm-crypt on the data volume
-- This cannot be enabled retroactively on RDS — set it from day one

-- Verify encryption is enabled:
-- SELECT setting FROM pg_settings WHERE name = 'data_checksums';
```

**At rest — object storage:**
```python
# MinIO/S3 bucket encryption config (set during bucket creation)
# For AWS S3: enable SSE-S3 (AES-256) or SSE-KMS as default encryption
# For MinIO in dev: encryption is optional but should mirror production

S3_ENCRYPTION_CONFIG = {
    "ServerSideEncryption": "aws:kms",    # production
    # "ServerSideEncryption": "AES256",   # alternative: SSE-S3
}
```

**At rest — secrets:**
- JWT private keys: stored in `keys/` directory, NEVER committed to git. `.gitignore` must include `keys/`.
- Environment variables: use `.env` files locally (gitignored), secrets manager (AWS Secrets Manager or Doppler) in production.
- Database credentials: rotate every 90 days (SOC 2 CC6.1 control).

**At rest — backups:**
```bash
# Automated daily PostgreSQL backups
# For AWS RDS: automated backups with 7-day retention (enable at creation)
# For self-hosted:
pg_dump --format=custom --compress=9 nextballup > backup_$(date +%Y%m%d).dump
# Encrypt backup before storing:
gpg --symmetric --cipher-algo AES256 backup_$(date +%Y%m%d).dump
# Store encrypted backup in separate S3 bucket with versioning enabled
# Test restore monthly (document the test — SOC 2 evidence)
```

### Dependency Security Scanning

```yaml
# .github/workflows/security.yml
name: Security Scan
on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: '0 8 * * 1'  # Weekly Monday 8am UTC

jobs:
  python-audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync
      - run: uv run pip-audit --strict  # Fails on known vulnerabilities

  npm-audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cd apps/web && pnpm audit --audit-level=high

  license-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync
      - run: uv run pip-licenses --fail-on="GPL;AGPL;SSPL"  # Block copyleft
```

**Rules:**
- `pip-audit` must pass with zero known vulnerabilities before merge to main.
- `pip-licenses` must confirm no AGPL/GPL/SSPL dependencies.
- Dependabot or Renovate enabled for automated dependency update PRs.
- Review and merge security patches within 72 hours (SOC 2 CC7.1).

### Data Retention and Deletion

```python
# packages/core/retention.py
"""Data retention policies aligned with SOC 2 and privacy regulations.

These are enforced by scheduled Celery tasks, NOT manual processes.
"""

RETENTION_POLICIES = {
    # Audit logs: minimum 1 year SOC 2, recommend 3 years
    "audit_logs": {
        "retain_days": 1095,  # 3 years
        "action": "archive_to_cold_storage",  # never delete audit logs, archive them
    },
    
    # Raw video: move to cold storage after 30 days, archive after 1 year
    "raw_video": {
        "hot_days": 30,
        "warm_days": 365,
        "cold_action": "glacier_deep_archive",
    },
    
    # Processed video (mezzanine + HLS): keep hot for active season
    "mezzanine_video": {
        "hot_days": 180,
        "warm_days": 365,
        "cold_action": "glacier_deep_archive",
    },
    
    # Player tracks / ball tracks: keep for season + 1 year
    "tracking_data": {
        "retain_days": 730,  # 2 years
        "action": "delete",  # can be reprocessed from video if needed
    },
    
    # User accounts: retain until deletion request
    # On deletion request: anonymize within 30 days (GDPR/CCPA)
    "user_deletion": {
        "anonymize_days": 30,
        "fields_to_null": ["email", "full_name", "phone", "avatar_url"],
        "fields_to_hash": [],  # hashed email kept for dedup prevention
        "audit_log_action": "retain",  # never delete audit log entries even on user deletion
    },
    
    # Player data on team removal: anonymize tracking linkage
    "team_removal": {
        "action": "anonymize_tracking",  # set player_id to null on player_tracks
        "retain_aggregate_metrics": True,  # PlayerGameMetrics kept but player_id nulled
    },
}
```

### SOC 2 Readiness Roadmap

| Phase | Timing | Controls to Implement | Evidence Generated |
|---|---|---|---|
| **MVP** | Month 0-4 | Audit log table + middleware, structured JSON logging, encryption at rest (DB + S3), dependency scanning in CI, `.gitignore` for secrets, RBAC enforcement, signed video URLs, bcrypt passwords, JWT rotation | Audit log entries, CI scan results, git history showing security-first design |
| **Pilot** | Month 3-6 | Written security policy (Information Security Policy, Acceptable Use, Incident Response Plan), automated daily backups with monthly restore tests, rate limiting + anomaly detection, access review process (quarterly), onboard Vanta or Drata for continuous monitoring | Policy documents, backup restore test reports, access review records, Vanta/Drata dashboard |
| **Scale** | Month 6-12 | SOC 2 Type I engagement (CPA firm, $15K-$30K), penetration test (annual, $10K-$20K), formal change management process, vendor risk assessments for all third-party services, employee security training | Type I report, pentest report, change tickets, vendor assessment records, training completion certificates |
| **Ongoing** | Month 12+ | SOC 2 Type II observation period (6-12 months), continuous control monitoring, annual pentest, quarterly access reviews, incident response drills | Type II report (the goal), continuous monitoring evidence |

### Architecture Tree Updates

Add these files to the architecture:

```
├── packages/db/src/nextballup_db/models/
│   └── audit.py                 # AuditLog (append-only)
├── apps/api/
│   ├── middleware/
│   │   ├── audit.py             # AuditContext + flush middleware
│   │   ├── request_id.py        # X-Request-ID propagation
│   │   └── tenant_guard.py      # Enforce team_id filtering on all queries
│   └── logging_config.py        # Structured JSON formatter
├── .github/workflows/
│   └── security.yml             # pip-audit + npm audit + license check
```

### PostgreSQL Hardening (init-db.sql additions)

```sql
-- Append to infra/scripts/init-db.sql:

-- Prevent mutation of audit logs at the database level
-- This is a defense-in-depth control — even if app code has a bug,
-- the database itself blocks updates and deletes on audit_logs
CREATE OR REPLACE FUNCTION prevent_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit log entries cannot be modified or deleted';
END;
$$ LANGUAGE plpgsql;

-- These triggers will be applied after Alembic creates the audit_logs table.
-- Run manually after first migration:
-- CREATE TRIGGER no_audit_update BEFORE UPDATE ON audit_logs
--     FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
-- CREATE TRIGGER no_audit_delete BEFORE DELETE ON audit_logs
--     FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();

-- Row-level security for tenant isolation (defense-in-depth)
-- Applied per-table after Alembic creates them:
-- ALTER TABLE games ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY games_tenant_isolation ON games
--     USING (team_id = current_setting('app.current_team_id')::uuid);
```

## Definition of Done

A feature is done when:
1. API endpoint exists with request validation, auth, and error handling
2. Database migration created and tested (up and down)
3. Unit tests cover happy path + at least 2 error cases
4. Integration test covers the full request lifecycle
5. Pydantic schemas document the API contract
6. Frontend component renders and handles loading/error states
7. No new Ruff or mypy warnings introduced
8. Audit log entries emitted for all state-changing operations (see audit action taxonomy above)
9. No secrets, tokens, or PII logged in plaintext
10. Dependency licenses verified (no AGPL/GPL/SSPL additions)

## E2E Architecture — 11 Layers

### Layer 1: Capture
iPhone/Android, tripod camera, multi-cam rig, or scoreboard feed → video file (MP4/MOV/MKV, up to 10GB). MVP accepts raw file upload with zero capture-side intelligence. Future: capture app with court detection overlay and framing guidance.

### Layer 2: Ingestion
Coach calls `POST /videos/upload` → receives presigned S3/MinIO PUT URL (1-hour expiry) → client uploads directly to storage (never through API server) → calls `POST /videos/{id}/complete` with SHA-256 checksum → server verifies integrity, enqueues transcode. **Orphan cleanup**: a scheduled task scans for `pending_upload` videos older than 2 hours and either verifies the S3 object exists (triggers processing) or cleans up the database record.

### Layer 3: Storage (S3/MinIO)
Six buckets: `nbu-raw-video` (originals, 30d hot → 1yr warm → glacier), `nbu-mezzanine` (1080p H.264, season hot → 1yr warm → glacier), `nbu-hls` (playback segments, season hot → 1yr warm → delete), `nbu-clips` (generated, indefinite), `nbu-thumbnails` (indefinite), `nbu-reports` (1yr hot → 3yr warm → delete). All buckets SSE-KMS encrypted, versioning on raw + mezzanine. Access exclusively via signed URLs — no public bucket access ever.

### Layer 4: Processing (Celery + GPU workers)
Six sequential idempotent stages, each a separate Celery task:

1. **Transcode** — FFmpeg → 1080p/30fps/H.264/CRF18 mezzanine + HLS 6s chunks + thumbnail.
2. **Detect** — RF-DETR on every frame. Classes: player, basketball, hoop/backboard, referee. Tiling for small ball detection. Confidence 0.3 players, 0.2 ball.
3. **Track** — BoT-SORT (BoxMOT) assigns persistent track IDs. Camera motion compensation. SigLIP embeddings for team color clustering. SmolVLM2 for jersey OCR on cropped bboxes.
4. **Court Map** — Detect 15+ court keypoints → RANSAC homography → map all tracks to canonical court coordinates (94ft × 50ft). Re-estimate per 5-second segment. Quality score from reprojection error. **Fallback**: if <4 keypoints detected, offer manual 4-point calibration UI. Degrade gracefully: disable spatial metrics, still deliver box score stats.
5. **Events** — Heuristic layer (possession changes, shot attempts, rebounds, turnovers) + ML classifier (MMAction2/PySlowFast for tactical actions: PnR, DHO, flare, cut, iso, spot-up). **Conflict resolution**: ML wins when confidence > 0.8, heuristic wins when ML < 0.5, both flagged for review between 0.5-0.8. Actor assignment via court-coordinate proximity.
6. **Metrics** — Box score, shooting by zone, Spatial IQ components, conversion rates, predictive features (shot quality, pass risk), tendency cards, defensive metrics. All written to player_game_metrics. **Suppress metrics computed from fewer than 10 possessions** — small sample unreliability.

### Layer 5: Data
PostgreSQL 16 (23 tables including audit log), Redis 7 (cache db0, Celery broker db1, Celery results db2), append-only audit log (3-year retention, monthly partitions, immutability triggers).

### Layer 6: API (FastAPI)
RS256 JWT auth (15min access, 7d refresh), RBAC (coach/player/admin), tenant isolation at query level, 50+ REST endpoints, WebSocket processing status via **Redis pub/sub message bus** (enables multi-instance API scaling), signed video URLs, rate limiting.

### Layer 7: Frontend (Next.js 15)
Film room (HLS player + event timeline), dashboards (Spatial IQ gauges, conversion funnels, shot charts, heatmaps), player profiles (tendency cards, shooting zones), clips/playlists, scouting reports, lineup analysis.

### Layer 8: Users
Coaches (upload, analyze, scout), players (view own profile/clips), recruiters (shared links, no account required).

### Layer 9: Security (cross-cutting)
TLS 1.2+ in transit, AES-256 at rest (DB + S3), audit trail on every mutation, RBAC + tenant isolation, COPPA age-gating, FERPA restrictions on k12_school institutions, BIPA consent for biometric data, SOC 2 controls from day one.

### Layer 10: Operations
CI/CD (GitHub Actions), structured JSON logging, dependency + license + secret scanning, daily backups with monthly restore tests, model registry (W&B artifacts), Dependabot with 72-hour patch SLA.

### Layer 11: Expansion
Volleyball (month 12-18), real-time live processing, mobile native app, partner APIs/SDKs.

## CV Pipeline Constraints (from audit)

These constraints are mandatory and must be followed when implementing `packages/cv_pipeline`:

- **Ball detection state machine**: Implement a 3-state tracker (TRACKING → OCCLUDED → LOST). In OCCLUDED state, use Kalman filter extrapolation to bridge gaps of 5-15 frames. In LOST state (>15 frames), enter reacquire mode using trajectory prediction. A detection that exists for only 1 frame is noise — require 3+ consecutive frames to confirm a new ball track.
- **Court registration fallback**: If fewer than 4 keypoints are detected, disable spatial metrics for that segment but continue processing box score stats. Expose a manual calibration endpoint where coach marks 4 court corners on a frame.
- **Identity persistence**: Track IDs will break during dead balls and camera cuts. Implement multi-cue reassociation: jersey number (when available, confidence-weighted) + team color embedding + relative height + position history. Budget 4-6 weeks for ReID tuning.
- **Pose minimum**: Skip pose estimation on player bounding boxes shorter than 80px. Below this threshold, keypoint accuracy drops below useful levels.
- **Confidence propagation**: All downstream metrics must carry a quality score derived from tracking quality. If tracking HOTA for a game is below 0.50, flag the game as "low confidence" in the UI.
- **Device auto-detection**: Never hardcode `mps`. On startup, probe: `torch.backends.mps.is_available()` → `torch.cuda.is_available()` → fallback to CPU. Log the selected device.
- **Model acceptance criteria** (must pass before shipping):
  - Player tracking HOTA > 65 on gym validation set
  - Ball track recall > 0.70 at 30fps
  - Event F1 > 0.80 for shots, rebounds, turnovers
  - Court homography reprojection error < 2.0 feet on validated keypoints

## Legal Requirements (from audit)

These are not optional and must be implemented before accepting real user data:

- **COPPA**: Users under 13 cannot register without verifiable parental consent. Age determination via date of birth at registration. Consent mechanism must be reviewed by children's privacy attorney. Block launch for any team with players under 13 until this is implemented.
- **FERPA**: Before onboarding any K-12 school, a FERPA school official agreement must be signed specifying legitimate educational interest, direct control by school, and no redisclosure without consent. Template must be drafted by counsel.
- **BIPA**: For Illinois-based users, biometric consent must be BIPA-specific: written policy publicly available, informed written consent before collection, disclosure of purpose and duration. Generic checkbox is not sufficient.
- **Data ownership**: Uploading entity (team/institution) owns raw video and basic stats. Players have right to access and export their own individual performance data. Derived analytics (Spatial IQ, tendency cards) are NextBallUp IP.
- **Model training governance**: Default opt-out for using customer data to train global models. Explicit disclosure in Terms of Service. Budget $15,000-$30,000 for pre-launch legal deliverables (ToS, Privacy Policy, COPPA flow, FERPA template, BIPA consent).
- **Shared links**: Must expire (30-day max), require player consent before generation (if player has account), include watermark with generating coach identity.

## Compliance Documentation Required (before pilot)

1. Information Security Policy (3-5 pages)
2. Acceptable Use Policy (1-2 pages)
3. Incident Response Plan (2-3 pages) with P1-P4 classification, roles, communication templates
4. Data Classification Policy (Public / Internal / Confidential / Restricted)
5. Vendor Risk Register (spreadsheet)
6. Access Review Log (quarterly, spreadsheet)
7. Change Management Policy (reference GitHub branch protection + CI requirements)

## Blocking Action Items

These must be resolved before MVP ships:

1. Partition `player_tracks` and `ball_tracks` tables by video_id (216M rows after 100 games without it)
2. Implement orphaned upload cleanup scheduled task
3. Add Redis pub/sub as WebSocket message bus for multi-instance API
4. Add health check endpoints: `GET /health`, `GET /health/ready`, `GET /health/live`
5. Implement CV device auto-detection (MPS → CUDA → CPU)
6. Design and implement ball detection state machine with reacquire logic
7. Implement court registration fallback (manual 4-point calibration)
8. Define event detection conflict resolution (heuristic vs ML priority rules)
9. Define and test model acceptance criteria (HOTA > 65, event F1 > 0.80)
10. COPPA-compliant consent flow (requires legal counsel)
11. Draft Terms of Service and Privacy Policy (requires legal counsel)
12. Configure W&B experiment tracking, DVC data versioning, CVAT annotation tool
13. Add ONNX export path to CV pipeline (PyTorch → ONNX → CoreML for Mac / TensorRT for cloud)

## Second-Pass Audit Findings (integrated)

These findings were identified by a second deep review after the first audit items were resolved. They address subtler issues that only surface when examining the integrated spec.

### Engineering (Senior SWE)

- **Resumable uploads are missing.** A single presigned PUT to S3 does not support resume on failure for large files. For files over 1GB (common for full games), use S3 multipart upload: the API returns an upload ID + presigned URLs for each 100MB chunk, the client uploads chunks in parallel, and calls complete-multipart on finish. Without this, coaches on gym WiFi will experience frequent upload failures on full-game footage. Implement via `CreateMultipartUpload` → `UploadPart` (presigned per part) → `CompleteMultipartUpload`.
- **UUID primary keys and HASH partitioning conflict.** PostgreSQL HASH partitioning on a UUID column works for write distribution but makes range queries impractical (you can't query "all tracks in the last 24 hours" across partitions efficiently). Switch to RANGE partitioning on a `created_date` column with monthly partitions for player_tracks and ball_tracks. Add a composite index on `(video_id, frame_number)` within each partition.
- **No dead letter queue for permanently failing tasks.** If a Celery task fails after all retries (e.g., corrupted video that always crashes FFmpeg), it currently disappears. Add a `failed_jobs` table or Redis-backed dead letter queue. Expose `GET /admin/failed-jobs` for monitoring. Alert on failed task accumulation.
- **Race condition on concurrent uploads.** Two coaches uploading to the same game simultaneously could produce conflicting processing jobs. Add a database advisory lock on `game_id` when creating processing jobs: `SELECT pg_advisory_xact_lock(hashtext(game_id::text))`. Only one video per game can enter processing at a time; subsequent uploads queue behind it.
- **Redis single instance is a SPOF.** Using one Redis for cache (db0), broker (db1), and results (db2) means a Redis crash kills caching, task processing, AND result retrieval simultaneously. For MVP this is acceptable, but document that production requires Redis Sentinel or separate instances for broker vs cache. Add this to the Scale phase infrastructure plan.
- **Graceful worker shutdown.** If a Celery worker is killed mid-processing (deploy, crash, OOM), the in-progress task's processing_job record stays in `running` status forever. Add: (a) a heartbeat column on processing_jobs updated every 60s by the worker, (b) a scheduled task that marks jobs with stale heartbeats (>5 minutes) as `failed` with error "worker lost," (c) Celery's `acks_late=True` on processing tasks so the broker redelivers unacknowledged tasks.
- **No API versioning strategy.** The spec uses `/api/v1` but doesn't define what happens when v2 ships. Decision: URL-based versioning (`/api/v2`), old versions supported for 12 months after deprecation notice, version sunset communicated via `Sunset` HTTP header on v1 responses.
- **Player tracks pagination.** Even with partitioning, querying 2.16M rows per game for the film room timeline is impractical. Never query raw tracks from the API. Instead, compute and cache per-possession summary snapshots during the Metrics stage: positions at 1-second intervals, key movement events, and zone transitions. Serve these summaries from the API; raw tracks are internal to the CV pipeline and metrics computation only.

### ML / CV (Senior ML Engineer)

- **30fps processing is wasteful.** Most tracking analytics don't need every frame. Process detection at 10fps for tracking (sufficient for player movement, saves 3× compute), upsample to 30fps only for ball detection windows (±2 seconds around potential shot events) and pose estimation on key frames. This alone cuts GPU processing time from ~4 hours to ~1.5 hours per game on the M5 Max.
- **Confidence calibration is unspecified.** RF-DETR's raw confidence scores are not probability-calibrated. A confidence of 0.7 doesn't mean "70% likely to be correct." Before using confidence scores for downstream decisions (conflict resolution, quality flagging), apply temperature scaling or Platt scaling on a held-out calibration set. Store both raw and calibrated confidence in the database.
- **Shot detection heuristic is underspecified.** "Ball trajectory toward hoop + release detection" requires: (a) defining "toward hoop" as ball court_z increasing AND ball court_xy moving within a 6-foot radius cone toward hoop position, (b) "release detection" as the frame where ball transitions from `in_hand` to `in_air` state (ball bbox no longer overlapping any player bbox for 3+ consecutive frames AND upward trajectory). Single-camera depth estimation makes court_z unreliable — fall back to 2D trajectory heuristics (ball moving upward in image coordinates toward hoop region) when court_z confidence is low.
- **Team color clustering will fail on similar uniforms.** SigLIP embeddings cluster well when teams have high-contrast colors (white vs dark blue), but fail when both teams wear similar shades (two shades of blue, or when one team's away jersey is similar to the other's home jersey). Require the coach to label one frame with "home team" and "away team" during upload as a seed for the clustering algorithm. This costs 5 seconds of coach time and eliminates the most common clustering failure mode.
- **Broadcast overlays are not handled.** Uploaded game film may contain burned-in scoreboards, watermarks, station logos, or ad banners. These are not player detections but can confuse the detector (logos misidentified as small objects) and occlude court keypoints. Add an overlay detection/masking step before court mapping: detect static regions (regions that don't change across 100+ frames) and mask them out of court keypoint detection.
- **No feedback loop from corrections to model improvement.** Coach event corrections (PATCH on events with `corrected: true`) are stored but never flow back to model training. Define the pipeline: (a) corrected events are exported weekly as supplemental training data, (b) tagged with correction type (false positive, false negative, wrong class, wrong actor), (c) added to the active learning queue for annotation review, (d) incorporated into the next fine-tuning cycle. Without this loop, the model never learns from its mistakes.
- **Multi-person pose confusion under the basket.** When 4-6 players are within a 6-foot radius (rebounds, post play, inbounds), ViTPose++ will assign keypoints to the wrong player. Add a post-processing step: after pose estimation, verify that each keypoint set's torso center falls within the corresponding player's bounding box. If >30% of keypoints fall outside the bbox, discard that pose estimate rather than propagating incorrect data.
- **GPU memory management on Apple Silicon.** MPS (Metal Performance Shaders) does not have the same memory management as CUDA. Specifically: (a) MPS doesn't support `torch.cuda.empty_cache()` — use `torch.mps.empty_cache()` instead, (b) MPS shared memory means large models compete with the OS for memory — set `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.7` to prevent OOM, (c) some operations fall back to CPU silently on MPS — profile with `PYTORCH_MPS_FALLBACK_POLICY=error` during development to catch these.

### Basketball Domain (NBA/Sports Analyst)

- **Score context is entirely absent.** The spec tracks `score_team` and `score_opponent` on the game record but not at the possession level. Every meaningful tendency analysis needs score differential context: a team's play calling when up 15 is completely different from when down 3. Add `score_differential` to the possession record (computed from detected scoring events). Gate all tendency analysis behind a score-context filter: "tendencies when leading," "tendencies when trailing," "tendencies in close games (±5)."
- **No clutch/garbage time filtering.** Metrics computed from the entire game include garbage time (final 3 minutes of a blowout), which distorts player tendencies. Define clutch as "final 5 minutes of regulation or OT with score differential ≤5." Add a `game_context` field to possessions: `normal`, `clutch`, `garbage_time`. Let coaches filter all dashboards by context. This is standard at the NBA level and coaches will expect it.
- **Film exchange is a missing workflow.** College programs share film with opponents (often via Hudl export). A coach wants to upload received opponent film, process it, and generate opponent scouting reports — but the opponent players don't have accounts in NextBallUp. Add a `game_type: "film_exchange"` where player identities are tracked by jersey number only (no user_id linkage), and all metrics roll up into an opponent profile rather than registered player profiles.
- **Possession definition is ambiguous on edge cases.** The spec doesn't address: (a) offensive rebound — does this start a new possession or continue the existing one? (Answer: continuation, per Synergy/NBA convention; the shot clock resets but the possession continues), (b) and-one plays — the free throw is part of the same possession, (c) technical free throws — not a possession, exclude from PPP calculations, (d) end-of-period heaves — tag as `end_of_period` and exclude from shooting statistics by default. Document these conventions in `packages/core/constants.py`.
- **The opponent model is one-directional.** You track your team's events but don't extract opponent tendencies from the same film. The same video contains both teams' plays. During event detection, tag each event with `offense_team: "team" | "opponent"`. Then mirror the entire tendency/metrics pipeline for the opponent side. This doubles the value of every uploaded game and is essential for scouting report generation.
- **Special situations are untagged.** Many coaching decisions hinge on situation type: after timeout (ATO), baseline out of bounds (BLOB), sideline out of bounds (SLOB), free throw alignment, press break, end-of-quarter set plays. These are detectable from game clock + dead ball events. Add a `situation_type` field to possessions. ATO sets are the #1 thing scouts look for because they reveal a team's most rehearsed plays.
- **Player fatigue proxy is needed.** A player's tendencies shift as they fatigue — shot selection becomes worse, defensive effort drops, decision speed slows. Track cumulative minutes within each game on the player track records and expose fatigue-aware tendency splits: "first half tendencies" vs "fourth quarter tendencies" as a minimum. The data is already available from the tracking pipeline.

### Legal (Legal Analyst — Round 2)

- **NIL (Name, Image, Likeness) rights.** NCAA athletes can now monetize their NIL. If NextBallUp generates highlight reels that players use for NIL deals, brand endorsements, or social media monetization, the platform facilitates commercial use of institutional video. The Terms of Service must: (a) clarify that the uploading institution controls commercial rights to game footage, (b) grant players a limited license to use their own clips for personal and NIL purposes, (c) indemnify NextBallUp against NIL disputes between players and institutions.
- **No liability limitation for AI-generated analysis.** If a coach benches a player or a recruiter passes on a prospect based on NextBallUp's shot quality score or Spatial IQ rating, and that analysis was wrong due to a tracking bug, the company faces potential negligence claims. Add to Terms of Service: (a) an explicit disclaimer that all analytics are AI-generated estimates, not definitive evaluations, (b) a statement that the platform is a decision-support tool and does not replace professional judgment, (c) a limitation of liability clause capping damages at the subscription fee paid. This is standard in analytics software but must be explicit.
- **Player data portability on transfer.** When a college player enters the transfer portal, they should be able to export their personal performance data (stats, tendency card, clips) to share with prospective programs. The spec grants players "right to access and export" but doesn't define: (a) the export format (PDF profile + MP4 clips + CSV stats), (b) whether the export includes data from all teams or only the requesting team, (c) whether the old institution can revoke access to game footage (they can — they own the video; but the player's derived stats are the player's data). Define this portability mechanism before onboarding any college program.
- **Anti-gambling clause.** Sports analytics platforms are targets for gambling syndicates seeking edge data. Add to Terms of Service: (a) prohibition on using the platform or any exported data for sports betting, gambling, or wagering purposes, (b) right to terminate accounts suspected of gambling-related use, (c) prohibition on automated data extraction (scraping, API abuse) for non-authorized purposes.
- **Coach departure data rights.** When a head coach leaves a program, do they retain access to the team's historical film and analytics? The answer should be no — the institutional account owns the data, and coach access is revoked when they're removed from the team. But the coach should be able to export their own coaching notes, custom alerts, and playlist configurations (their intellectual work product). Document this distinction.

### Compliance (Compliance Analyst — Round 2)

- **Disaster recovery RTO/RPO targets.** Define now so architecture supports them: RTO (Recovery Time Objective) of 4 hours and RPO (Recovery Point Objective) of 1 hour for the database. For video storage, RTO of 24 hours is acceptable (videos can be reprocessed). This means: PostgreSQL must have point-in-time recovery enabled with WAL archiving (1-hour granularity), not just daily pg_dump snapshots. AWS RDS handles this automatically; self-hosted requires WAL-G or pgBackRest.
- **Privileged access management.** Who can access production database directly (via psql)? Who can access the S3 buckets without going through the API? Document: (a) production access limited to 2 named individuals (founder + one engineer), (b) access via SSH bastion host with MFA, (c) all production queries logged to the audit trail, (d) quarterly review of privileged access (who still needs it?). This is CC6.3 evidence for SOC 2.
- **Audit log cross-partition queries.** Monthly partitioning is correct for write performance, but security investigations need to query across months ("show me all actions by user X in the last 6 months"). Ensure the partition key allows efficient cross-partition index scans, or maintain a materialized view of recent audit entries (last 90 days) for fast investigation queries.
- **Account lockout and password history.** The spec defines rate limiting (5 auth attempts/minute) but not: (a) account lockout after 10 failed attempts within 1 hour (lock for 30 minutes, notify user via email), (b) password history — prevent reuse of last 5 passwords, (c) session invalidation — when password changes, invalidate all existing refresh tokens. Add these to the auth service spec.
- **Data residency.** If you serve international customers (European basketball academies, FIBA programs), GDPR requires knowing where data is stored. Document: all data is stored in US-East-1 (or your chosen region). International customers must acknowledge US data residency in their agreement. If an EU customer requires EU data residency, this is a future infrastructure capability, not MVP scope.
- **Subprocessor list.** GDPR and many enterprise procurement processes require a list of subprocessors (third parties who process personal data on your behalf). Maintain a public page listing: cloud provider (AWS/GCP), email service (if any), analytics (W&B — confirm no PII in experiment logs), payment processor (Stripe). Update within 30 days of adding a new subprocessor.

### Research & Tooling (Tools Analyst — Round 2)

- **BoxMOT AGPL contamination risk.** The `boxmot` package bundles DeepSORT (GPL-3.0) alongside MIT trackers. Verify at import time that only MIT-licensed tracker code is loaded. Add a runtime check: `assert tracker_type in ["botsort", "bytetrack", "ocsort"]` before initializing BoxMOT. If BoxMOT's import mechanism loads DeepSORT code regardless of which tracker you select, you may need to vendor only the MIT tracker files directly instead of depending on the full package. Test this before committing to the dependency.
- **Missing dependencies in cv_pipeline pyproject.toml.** Add: `decord>=0.6` (Apache-2.0, fast video reading, 3× faster than OpenCV VideoCapture for batch extraction), `supervision>=0.25` (MIT, Roboflow's CV utilities for annotation visualization and format conversion), `av>=13.0` (BSD, PyAV for programmatic FFmpeg — more reliable than ffmpeg-python for complex transcoding).
- **No feature flag system.** When rolling out new metrics or model versions, you need gradual exposure to catch regressions before they affect all users. Add `flagsmith` (BSD-3-Clause) or implement a simple JSON-based feature flag config: `{"spatial_iq_composite": false, "defensive_metrics": false, "new_ball_tracker_v2": false}` stored in Redis and checked at API response time. This lets you enable experimental metrics for specific teams during pilot.
- **The training/ directory is empty.** Add structure: `training/detection/train.py` (RF-DETR fine-tuning script), `training/detection/export.py` (PyTorch → ONNX → CoreML), `training/tracking/eval.py` (TrackEval harness), `training/events/train.py` (action classifier), `training/configs/` (hyperparameter YAML files), `training/data/` (DVC-tracked dataset symlinks). Each training script should log to W&B and output versioned model artifacts.
- **No A/B testing for model versions.** When you ship a new detector or tracker version, you need to compare it against the previous version on the same footage. Implement a shadow processing mode: new model processes a game in parallel with the production model, results are compared on held-out gold set games, new model promoted only if metrics improve on all acceptance criteria. This prevents regressions that look good in isolation but fail on real gym footage.
- **Apple Silicon profiling.** Before optimizing, profile the pipeline on the M5 Max with `torch.mps.profiler.start()` / `stop()` and Instruments.app (Metal GPU profiler). Common MPS bottlenecks: (a) data transfer between CPU and GPU unified memory (use `tensor.to('mps', non_blocking=True)`), (b) operators that fall back to CPU (convolutions with unusual strides, certain attention variants), (c) memory fragmentation after long inference runs. Profile once with a full game and log per-stage wall-clock times in a `BENCHMARKS.md`.

## Third-Pass Audit Findings (integrated)

Focused on internal consistency, end-to-end user journey gaps, and issues introduced by the notes feature and Instagram-style redesign.

### Engineering (Round 3)

- **Variable frame rate video is unhandled.** Phone cameras (especially Android) frequently produce VFR (variable frame rate) files where the actual FPS fluctuates. The transcode stage must normalize to CFR (constant frame rate) before any CV processing. FFmpeg flag: `-vsync cfr` during transcode. Without this, frame-indexed tracks will desync from the video by seconds over a 2-hour game.
- **Signed URL refresh during playback.** HLS URLs expire after 1 hour. A coach reviewing film for 90 minutes will hit an expired URL mid-playback. The frontend must detect 403 responses from the CDN, transparently call `GET /videos/{id}` for a fresh signed URL, and update the HLS source without interrupting playback or losing the current timestamp. Add this to the `VideoPlayer.tsx` spec.
- **Pagination inconsistency.** The API spec says "cursor-based or offset" but doesn't define which endpoints use which. Decision: all list endpoints use offset pagination (`page` + `per_page`) for simplicity at MVP. Switch to cursor-based (keyset pagination on `created_at` + `id`) only for high-volume endpoints (events, notes, player_tracks summaries) when offset performance degrades. Document which endpoints use which in the API spec.
- **No caching strategy.** Redis db0 is designated for cache but no cache keys or TTLs are defined. Specify: player profile cache (TTL 5 minutes, invalidated on new game processing), game event list cache (TTL 1 minute during active review, infinite after game is "completed"), team roster cache (TTL 15 minutes), feature flags (TTL 30 seconds). Cache keys follow pattern `{team_id}:{entity}:{id}`.
- **Database seed data for development.** Add a `scripts/seed.py` that creates: 1 admin user, 2 coach users, 5 player users, 2 teams, 3 games with mock events/metrics/possessions. Without seed data, every developer spends 30 minutes manually creating accounts and teams before they can work on any feature.
- **CLAUDE.md is approaching Claude Code context limits.** At 845+ lines, CLAUDE.md is large. Claude Code reads this file first and holds it in context. If it grows past ~1000 lines, split into `CLAUDE.md` (architecture + conventions + constraints, ~400 lines) and `AUDIT_DECISIONS.md` (audit findings + rationale, remainder). Claude Code can be instructed to read both, but the primary file should stay lean.
- **Notes create discoverable records.** In litigation (e.g., a wrongful termination suit against a coach, or a Title IX investigation), notes attached to events become discoverable evidence. The retention policy must address: (a) notes are retained for the lifetime of the team + 1 year, (b) notes cannot be permanently deleted by users — only soft-deleted (hidden from UI but retained in database), (c) an admin export endpoint for e-discovery compliance. Add `deleted_at` (soft delete) to the Note model.

### ML / CV (Round 3)

- **10fps tracking + 30fps ball detection creates frame alignment issues.** When tracking runs at 10fps (every 3rd frame at 30fps source), track positions at intermediate frames don't exist. Ball detection at 30fps will reference frames where player positions are interpolated, not detected. Solution: run detection at 10fps for players AND ball. For ball trajectory analysis around shot events, run a second detection pass at 30fps on a ±3 second window centered on the event. This avoids the misalignment entirely and reduces total compute further.
- **Team color seed can be semi-automatic.** Requiring coach input on every upload adds friction. Better approach: auto-cluster team colors on the first 100 frames, present the result to the coach as a confirmation ("Is the home team wearing white? Yes/No"). Only interrupt the upload flow when clustering confidence is below 0.7. This reduces coach input to a single tap in 80%+ of cases and eliminates it entirely when teams have high-contrast uniforms.
- **Model warm-up on cold start.** The first inference on MPS takes 10-30 seconds while PyTorch compiles the computation graph for the Metal backend. Add an explicit warm-up step in the worker startup: run inference on a single dummy frame before accepting tasks. This prevents the first real game from showing abnormally long processing times for the detection stage. Add `CV_WARMUP=true` to `.env.example`.
- **Lens distortion correction.** Phone cameras have significant barrel distortion, especially ultra-wide lenses. This distorts court lines and makes homography estimation less accurate. Add an optional distortion correction step before court mapping: if camera EXIF data includes lens model, apply known distortion coefficients. If not, estimate from detected court lines (straight lines that appear curved indicate distortion). OpenCV's `cv2.undistort()` handles this with calibration parameters.
- **The pose keypoint bbox check needs tuning.** The rule "discard if >30% of keypoints fall outside bbox" is too aggressive for players in fast lateral movement (the body extends beyond the detection bbox during crossovers and defensive slides). Relax to: discard if the torso center (midpoint of shoulders and hips) falls outside the bbox. Individual limb keypoints extending beyond the bbox is expected and normal.

### Basketball Domain (Round 3)

- **Notes should support @mentions with role-aware delivery.** When a coach @mentions a player on a specific event, the player should see a notification card in their feed: "[Coach Johnson] tagged you on a play: 'Great closeout — this is the timing we want.'" This turns notes into a coaching tool, not just annotations. When a player @mentions a coach, it appears in the coach's notification bell. Player-to-player mentions on the same team are allowed but subject to minor safeguarding review.
- **Film room needs a comparison mode.** Coaches frequently want to show a player "here's what you did, here's what you should do" — two clips side by side. Add a split-screen mode in the film room: left clip + right clip, synced or independent playback. This is a V1 feature but the video player component architecture should support it from day one (two `VideoPlayer` instances sharing a `ComparisonController`).
- **Tendency cards must show sample size prominently.** A tendency card showing "drives left 85% of the time" is misleading if it's based on 7 possessions. Every tendency metric must display sample size next to the value: "Drives left: 85% (n=47)" vs "Drives left: 85% (n=7)." Suppress or gray out any tendency computed from fewer than 15 occurrences. This is the single most common criticism of advanced analytics from coaches — numbers presented without context.
- **Game plan preparation workflow.** The most valuable coaching use case after film review is game plan preparation: "Our next opponent runs Spain PnR 40% of the time — here's how they execute it, here are the 3 clips to show the team." This workflow needs: (a) an opponent profile page aggregating data from film_exchange games, (b) a "game plan" document builder where the coach selects opponent tendencies, attaches clips, and writes notes, (c) a share-to-team action that pushes the game plan to all players' feeds before the game. This is V1 but the opponent profile data model should support it from MVP.
- **Search needs basketball vocabulary.** The search feature must understand basketball terminology: "corner three" should match events with `zone: "three_left_corner" OR "three_right_corner"`, "PnR" should match `tactical_tag: "pick_and_roll"`, "James drives" should match events with `actor: James AND event_type: dribble_drive`. Build a basketball synonym dictionary in `packages/core/constants.py` and apply it as query expansion in the search endpoint.

### Legal (Round 3)

- **Notes are discoverable records.** In any legal proceeding involving the team (Title IX investigation, wrongful termination, discrimination claim), notes attached to game events become discoverable evidence. A coach who writes "Player X isn't fast enough for this level" in a note creates a record that could surface in a discrimination lawsuit. Mitigations: (a) add a disclaimer in the notes UI: "Notes may be subject to legal discovery," (b) implement soft-delete only (users can hide notes but not permanently destroy them), (c) build an admin-only bulk export endpoint for legal compliance, (d) include notes in the data retention policy.
- **FERPA intersection with notes.** If `institution_type == "k12_school"`, notes about students are education records under FERPA. This means: (a) the student (or parent if under 18) has the right to inspect notes about them, (b) the student can request amendment of inaccurate notes, (c) notes cannot be disclosed to third parties without consent. The notes API must support a `GET /notes?about_user_id=uuid` query that returns all notes mentioning a specific player — this is the FERPA access mechanism.

### Compliance (Round 3)

- **Notes create additional PII that needs classification.** Notes mentioning players by name + performance assessment = Confidential data. Add notes to the data classification matrix. Notes containing health-related observations ("Player X looked like they were favoring their left knee") are Restricted — they could constitute health information with implications under state privacy laws. Consider adding a "health/injury" tag option on notes that triggers elevated retention and access controls.
- **Soft-delete complicates GDPR right to erasure.** If a user requests account deletion under GDPR/CCPA, their notes must be anonymized (author set to "deleted user") but the note content must be evaluated — if it contains PII about other players, the content itself may need redaction. Add a `anonymize_author()` method on the Note model that: (a) sets author_id to null, (b) replaces author display name with "Former team member," (c) scans body text for @mention patterns and replaces with "[team member]."

### Research & Tooling (Round 3)

- **Frontend needs Framer Motion.** The Instagram-style shared element transitions (game card expanding into game detail, clip grid thumbnail opening into full player) require `framer-motion` (MIT). Add to `apps/web/package.json`. Specifically needed: `AnimatePresence` for page transitions, `layoutId` for shared element animations, `useScroll` for feed scroll-based effects. Without this, the Instagram UX falls back to hard page cuts that break the content-first feel.
- **Stories row state management.** The "unviewed" gradient ring on game stories needs per-user state: which games has this user opened since the last event was processed? Store in Redis: `stories:viewed:{user_id}:{game_id} = last_viewed_timestamp`. Compare against `game.processing_metadata.last_completed_at`. Don't put this in PostgreSQL — it's high-frequency read/write ephemeral state.
- **Add `framer-motion` and `@tanstack/react-virtual` to frontend deps.** The feed and stories row will have many items — use virtualization (`react-virtual`) to avoid rendering hundreds of DOM nodes for the game feed. The clip grid also needs virtualization for teams with 500+ clips across a season.
