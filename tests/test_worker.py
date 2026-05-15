"""Worker runtime tests.

These exercise the async runtime functions directly against the test DB
session, bypassing Celery. The API-side setup is reused from test_videos so
RLS-gated INSERTs go through the audited code paths.
"""

from __future__ import annotations

import json
import os
import stat
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from nextballup_api.routers.videos import get_storage
from nextballup_api.storage import (
    PresignedPart,
    PresignedUpload,
    StoragePresigner,
    storage_key_for_mezzanine,
)
from nextballup_worker.errors import (
    PermanentProcessingError,
    TransientProcessingError,
)
from nextballup_worker.runtime import (
    claim_job,
    cleanup_abandoned_uploads,
    cleanup_email_verification_tokens,
    cleanup_expired_raw_videos,
    cleanup_password_reset_tokens,
    complete_job,
    dispatch_pending_jobs,
    execute_cv_stage,
    execute_demo_preview,
    execute_transcode,
    fail_job,
    finalize_demo_preview_failure,
    recover_stale_jobs,
    release_job_for_retry,
    retry_raw_video_storage_deletes,
    touch_heartbeat,
)
from nextballup_worker.runtime.media import (
    BrowserMezzanineArtifact,
)
from nextballup_worker.runtime.media import (
    create_browser_mezzanine as real_create_browser_mezzanine,
)
from nextballup_worker.tasks import _ensure_runtime_broker_configured
from nextballup_worker.tenant import (
    clear_worker_context,
    set_worker_context,
    set_worker_operator_role,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.demo_preview import (
    DemoPreviewArtifact,
    cleanup_expired_demo_previews,
    mark_demo_preview_running,
    resolve_demo_preview,
    resolve_demo_preview_state,
)
from nextballup_core.enums import (
    CameraHeight,
    CameraPosition,
    GameType,
    InstitutionType,
    ProcessingJobStage,
    ProcessingJobStatus,
    ReviewStatus,
    Sport,
    TeamLevel,
    UploadMethod,
    VideoEventType,
    VideoStatus,
)
from nextballup_core.errors import ConflictError, ServiceUnavailableError
from nextballup_core.settings import Settings, get_settings, reload_settings
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.billing import UsageEvent
from nextballup_db.models.cv import VideoEvent
from nextballup_db.models.email_verification import EmailVerificationToken
from nextballup_db.models.game import Game
from nextballup_db.models.password_reset import PasswordResetToken
from nextballup_db.models.team import Team
from nextballup_db.models.user import User
from nextballup_db.models.video import ProcessingJob, Video
from scripts.configure_runtime_db_role import _set_runtime_role_password

API = "/api/v1"
_MP4_PAYLOAD = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
_PNG_PAYLOAD = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


# ---- Fake storage ---------------------------------------------------------


class FakeWorkerStorage:
    """Storage fake used for both API seeding and worker head_object calls."""

    def __init__(self) -> None:
        self.object_sizes: dict[str, int] = {}
        self.object_metadata: dict[str, dict[str, str]] = {}
        self.pending_multiparts: dict[str, tuple[str, int]] = {}
        self.aborted_multiparts: list[dict[str, str]] = []
        self.completed_multiparts: list[dict[str, Any]] = []
        self.download_payload = _MP4_PAYLOAD + b"fake-video"
        # Toggle to simulate object gone missing mid-pipeline.
        self.drop_keys: set[str] = set()
        self.head_fail_keys: set[str] = set()
        self.delete_fail_keys: set[str] = set()
        self.abort_fail_upload_ids: set[str] = set()

    def is_configured(self) -> bool:
        return True

    def presign_upload(
        self,
        *,
        key: str,
        content_type: str,
        file_size_bytes: int,
        checksum_sha256: str | None = None,
    ) -> PresignedUpload:
        if file_size_bytes <= 1_073_741_824:
            self.object_sizes[key] = file_size_bytes
            return PresignedUpload(
                method=UploadMethod.PUT,
                url=f"https://fake-storage.test/{key}",
                headers={"Content-Type": content_type},
            )
        upload_id = f"fake-upload-{uuid.uuid4().hex[:8]}"
        self.pending_multiparts[upload_id] = (key, file_size_bytes)
        return PresignedUpload(
            method=UploadMethod.MULTIPART,
            upload_id=upload_id,
            parts=(PresignedPart(part_number=1, url="https://example/part1"),),
            part_size_bytes=100 * 1024 * 1024,
        )

    def complete_multipart(self, *, key: str, upload_id: str, parts: list[dict[str, Any]]) -> None:
        self.completed_multiparts.append({"key": key, "upload_id": upload_id, "parts": parts})
        entry = self.pending_multiparts.pop(upload_id, None)
        if entry is not None:
            _, size = entry
            self.object_sizes[key] = size

    def abort_multipart(self, *, key: str, upload_id: str) -> None:
        if upload_id in self.abort_fail_upload_ids:
            from nextballup_api.storage import StorageFailureError

            raise StorageFailureError("Simulated multipart abort failure", details={"key": key})
        self.aborted_multiparts.append({"key": key, "upload_id": upload_id})
        self.pending_multiparts.pop(upload_id, None)

    def delete_object(self, *, key: str) -> None:
        if key in self.delete_fail_keys:
            from nextballup_api.storage import StorageFailureError

            raise StorageFailureError("Simulated delete failure", details={"key": key})
        self.object_sizes.pop(key, None)

    def head_object(self, *, key: str) -> dict[str, Any] | None:
        if key in self.head_fail_keys:
            from nextballup_api.storage import StorageFailureError

            raise StorageFailureError("Simulated storage failure", details={"key": key})
        if key in self.drop_keys:
            return None
        size = self.object_sizes.get(key)
        if size is None:
            return None
        # Synthesize a 32-hex-char ETag so the worker's MD5-shaped detector
        # treats it as a single-part upload (matches how MinIO returns it
        # for files under the multipart threshold).
        synthetic_md5 = (key.encode("utf-8").hex().ljust(32, "0"))[:32]
        return {
            "ContentLength": size,
            "ETag": f'"{synthetic_md5}"',
            "Metadata": self.object_metadata.get(key, {}),
        }

    def presign_get(
        self, *, key: str, expires_in: int, response_content_type: str | None = None
    ) -> str:
        ct_param = f"&rct={response_content_type}" if response_content_type else ""
        return f"https://fake-storage.test/{key}?X-Get=1&exp={expires_in}{ct_param}"

    def download_file(self, *, key: str, destination: str) -> None:
        Path(destination).write_bytes(self.download_payload)

    def upload_file(
        self,
        *,
        key: str,
        source: str,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.object_sizes[key] = Path(source).stat().st_size
        self.object_metadata[key] = dict(metadata or {})


# ---- Fixtures -------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def fake_storage() -> FakeWorkerStorage:
    return FakeWorkerStorage()


@pytest_asyncio.fixture(loop_scope="session")
async def storage_client(
    db_session: AsyncSession, fake_storage: FakeWorkerStorage
) -> AsyncIterator[AsyncClient]:
    from nextballup_api.deps import get_db
    from nextballup_api.main import app

    from nextballup_core.settings import reload_settings
    from tests.csrf_helper import make_csrf_mirror_hook

    reload_settings()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_storage() -> StoragePresigner:
        return fake_storage

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = _override_storage
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            event_hooks={"request": [make_csrf_mirror_hook()]},
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def demo_preview_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Path]]:
    training_root = tmp_path / "training"
    script_path = training_root / "scripts" / "local_demo_infer.py"
    config_path = training_root / "configs" / "demo.yaml"
    checkpoint_path = training_root / "checkpoints" / "demo.pth"
    preview_root = tmp_path / "demo_previews"

    script_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    script_path.write_text("print('stub demo infer')\n", encoding="utf-8")
    config_path.write_text("id: demo\n", encoding="utf-8")
    checkpoint_path.write_bytes(b"checkpoint")

    monkeypatch.setenv("CV_DEMO_PREVIEW_ENABLED", "true")
    monkeypatch.setenv("CV_DEMO_PREVIEW_ROOT", str(preview_root))
    monkeypatch.setenv("CV_DEMO_TRAINING_REPO_ROOT", str(training_root))
    monkeypatch.setenv("CV_DEMO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CV_DEMO_CHECKPOINT_PATH", str(checkpoint_path))

    reload_settings()
    try:
        yield {
            "training_root": training_root,
            "preview_root": preview_root,
            "config_path": config_path,
            "checkpoint_path": checkpoint_path,
        }
    finally:
        reload_settings()


# ---- Helpers --------------------------------------------------------------


def _coach(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "password": "Password1!",
        "full_name": "Mike Johnson",
        "role": "coach",
    }


def _team_body() -> dict[str, Any]:
    return {
        "name": "Lincoln Varsity Boys",
        "sport": "basketball",
        "level": "high_school",
        "institution": "Lincoln High School",
        "institution_type": "k12_school",
        "season": "2026-2027",
    }


def _game_body(team_id: str) -> dict[str, Any]:
    return {
        "team_id": team_id,
        "opponent_name": "Jefferson Eagles",
        "game_type": "regular_season",
        "date": "2026-11-15",
        "time": "19:00",
        "location": "Lincoln High Gym",
        "is_home": True,
        "periods": 4,
        "period_length_minutes": 8,
        "notes": "District opener",
    }


def _shot_clock_game_body(team_id: str) -> dict[str, Any]:
    body = _game_body(team_id)
    body["shot_clock_enabled"] = True
    body["shot_clock_seconds"] = 30
    return body


def _upload_body(game_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "game_id": game_id,
        "filename": "lincoln_vs_jefferson_q1.mp4",
        "file_size_bytes": 250 * 1024 * 1024,
        "content_type": "video/mp4",
        "camera_position": "sideline",
        "camera_height": "elevated",
    }
    body.update(overrides)
    return body


async def _seed_queued_video(
    client: AsyncClient, *, coach_email: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (team_id, video_id, transcode_job_id) for a freshly queued video."""
    await client.post(f"{API}/auth/register", json=_coach(coach_email))
    team = (await client.post(f"{API}/teams", json=_team_body())).json()
    game = (await client.post(f"{API}/games", json=_game_body(team["id"]))).json()
    upload = (await client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))).json()
    complete = (
        await client.post(
            f"{API}/videos/{upload['id']}/complete",
            json={"checksum_sha256": "a" * 64},
        )
    ).json()
    return (
        uuid.UUID(team["id"]),
        uuid.UUID(complete["id"]),
        uuid.UUID(complete["job_id"]),
    )


async def _seed_committed_worker_video(
    engine: AsyncEngine,
    fake_storage: FakeWorkerStorage,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed a committed queued video outside the test session savepoint.

    Runtime-role worker tests use a separate database connection, so they
    cannot see rows inserted through the normal db_session fixture until those
    rows are committed on an independent owner connection.
    """
    team_id = uuid.uuid4()
    game_id = uuid.uuid4()
    video_id = uuid.uuid4()
    job_id = uuid.uuid4()
    raw_key = f"raw/{team_id}/{video_id}/runtime-worker.mp4"
    file_size = 250 * 1024 * 1024
    owner_sessionmaker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with owner_sessionmaker() as owner_session:
        await set_worker_context(owner_session, team_id=team_id)
        owner_session.add_all(
            [
                Team(
                    id=team_id,
                    name="Runtime Worker Audit Team",
                    sport=Sport.BASKETBALL,
                    level=TeamLevel.AAU_CLUB,
                    institution_type=InstitutionType.NONE,
                    season="2026",
                    invite_code=f"RWA{team_id.hex[:8].upper()}",
                ),
                Game(
                    id=game_id,
                    team_id=team_id,
                    opponent_name="Runtime Worker Opponent",
                    game_type=GameType.SCRIMMAGE,
                    date=datetime.now(tz=UTC).date(),
                    is_home=True,
                ),
                Video(
                    id=video_id,
                    game_id=game_id,
                    team_id=team_id,
                    filename="runtime-worker.mp4",
                    storage_key_raw=raw_key,
                    status=VideoStatus.QUEUED,
                    file_size_bytes=file_size,
                    content_type="video/mp4",
                    checksum_sha256="a" * 64,
                    camera_position=CameraPosition.SIDELINE,
                    camera_height=CameraHeight.ELEVATED,
                ),
                ProcessingJob(
                    id=job_id,
                    video_id=video_id,
                    team_id=team_id,
                    stage=ProcessingJobStage.TRANSCODE,
                    status=ProcessingJobStatus.PENDING,
                    progress_percent=0,
                ),
            ]
        )
        await owner_session.commit()
    fake_storage.object_sizes[raw_key] = file_size
    return team_id, video_id, job_id


async def _load_job(session: AsyncSession, job_id: uuid.UUID) -> ProcessingJob:
    await set_worker_operator_role(session)
    result = await session.execute(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .execution_options(populate_existing=True)
    )
    await clear_worker_context(session)
    row = result.scalar_one_or_none()
    assert row is not None, "ProcessingJob missing"
    return row


async def _load_video(session: AsyncSession, video_id: uuid.UUID) -> Video:
    await set_worker_operator_role(session)
    result = await session.execute(
        select(Video).where(Video.id == video_id).execution_options(populate_existing=True)
    )
    await clear_worker_context(session)
    row = result.scalar_one_or_none()
    assert row is not None, "Video missing"
    return row


async def _count_actions(session: AsyncSession, *, team_id: uuid.UUID, action: str) -> int:
    await set_worker_operator_role(session)
    count = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.team_id == team_id, AuditLog.action == action)
    )
    await clear_worker_context(session)
    return int(count or 0)


# ---- retention cleanup ----------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_email_verification_tokens_prunes_used_or_expired_tokens(
    storage_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    email = "email-token-cleanup@example.com"
    response = await storage_client.post(f"{API}/auth/register", json=_coach(email))
    assert response.status_code == 201, response.text
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    now = datetime.now(tz=UTC)
    expired = EmailVerificationToken(
        user_id=user.id,
        token_hash="1" * 64,
        expires_at=now - timedelta(days=8),
        created_at=now - timedelta(days=8),
    )
    used = EmailVerificationToken(
        user_id=user.id,
        token_hash="2" * 64,
        expires_at=now + timedelta(minutes=30),
        used_at=now,
        created_at=now - timedelta(minutes=5),
    )
    active = EmailVerificationToken(
        user_id=user.id,
        token_hash="3" * 64,
        expires_at=now + timedelta(minutes=30),
        created_at=now - timedelta(minutes=5),
    )
    db_session.add_all([expired, used, active])
    await db_session.flush()

    pruned = await cleanup_email_verification_tokens(
        db_session,
        settings=Settings(),
        request_id="test.email_token_cleanup",
    )
    assert pruned == 2
    remaining = await db_session.scalars(
        select(EmailVerificationToken.token_hash).where(
            EmailVerificationToken.token_hash.in_(["1" * 64, "2" * 64, "3" * 64])
        )
    )
    assert set(remaining.all()) == {"3" * 64}
    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.USER_EMAIL_VERIFICATION_TOKENS_PRUNED)
    )
    assert audit is not None
    assert (audit.extra or {}).get("count") == 2
    assert (audit.extra or {}).get("criterion") == "used_or_expired"


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_password_reset_tokens_prunes_used_or_expired_tokens(
    storage_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    email = "password-token-cleanup@example.com"
    response = await storage_client.post(f"{API}/auth/register", json=_coach(email))
    assert response.status_code == 201, response.text
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    now = datetime.now(tz=UTC)
    expired = PasswordResetToken(
        user_id=user.id,
        token_hash="a" * 64,
        expires_at=now - timedelta(minutes=1),
        created_at=now - timedelta(days=1),
    )
    used = PasswordResetToken(
        user_id=user.id,
        token_hash="b" * 64,
        expires_at=now + timedelta(minutes=30),
        used_at=now,
        created_at=now - timedelta(minutes=5),
    )
    active = PasswordResetToken(
        user_id=user.id,
        token_hash="c" * 64,
        expires_at=now + timedelta(minutes=30),
        created_at=now - timedelta(minutes=5),
    )
    db_session.add_all([expired, used, active])
    await db_session.flush()

    pruned = await cleanup_password_reset_tokens(
        db_session,
        settings=Settings(),
        request_id="test.password_token_cleanup",
    )
    assert pruned == 2
    remaining = await db_session.scalars(
        select(PasswordResetToken.token_hash).where(
            PasswordResetToken.token_hash.in_(["a" * 64, "b" * 64, "c" * 64])
        )
    )
    assert set(remaining.all()) == {"c" * 64}
    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.USER_PASSWORD_RESET_TOKENS_PRUNED)
    )
    assert audit is not None
    assert (audit.extra or {}).get("count") == 2
    assert (audit.extra or {}).get("criterion") == "used_or_expired"


# ---- claim_job ------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_transitions_pending_to_running(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team_id, _, job_id = await _seed_queued_video(
        storage_client, coach_email="claim-coach@example.com"
    )

    claimed = await claim_job(db_session, job_id=job_id, celery_task_id="t-1")
    assert claimed is not None
    assert claimed.status is ProcessingJobStatus.RUNNING
    assert claimed.celery_task_id == "t-1"
    assert claimed.started_at is not None
    assert claimed.heartbeat_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_skips_completed_job(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team_id, _, job_id = await _seed_queued_video(
        storage_client, coach_email="skip-coach@example.com"
    )
    # Short-circuit straight to COMPLETED
    await claim_job(db_session, job_id=job_id, celery_task_id="t-2")
    await complete_job(db_session, job_id=job_id, result_metadata={"ok": True})
    await db_session.commit()

    reclaim = await claim_job(db_session, job_id=job_id, celery_task_id="t-2-dup")
    assert reclaim is None


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_does_not_reclaim_live_running_job(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Duplicate delivery must not steal a live RUNNING job."""
    team_id, _, job_id = await _seed_queued_video(
        storage_client, coach_email="reclaim-coach@example.com"
    )
    first = await claim_job(db_session, job_id=job_id, celery_task_id="t-orig")
    assert first is not None
    second = await claim_job(db_session, job_id=job_id, celery_task_id="t-retry")
    assert second is None


@pytest.mark.asyncio(loop_scope="session")
async def test_release_for_retry_returns_running_job_to_pending(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, _, job_id = await _seed_queued_video(storage_client, coach_email="retry-release@example.com")
    claimed = await claim_job(db_session, job_id=job_id, celery_task_id="t-run")
    assert claimed is not None

    released = await release_job_for_retry(
        db_session,
        job_id=job_id,
        result_metadata={"attempt": 1, "last_error_code": "transient"},
    )
    assert released is not None
    assert released.status is ProcessingJobStatus.PENDING
    assert released.celery_task_id == "t-run"
    assert released.progress_percent == 0
    assert (released.result_metadata or {})["attempt"] == 1

    reclaimed = await claim_job(db_session, job_id=job_id, celery_task_id="t-retry")
    assert reclaimed is not None
    assert reclaimed.status is ProcessingJobStatus.RUNNING
    assert reclaimed.celery_task_id == "t-retry"


# ---- heartbeat + completion ----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_touch_heartbeat_updates_timestamp(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team_id, _, job_id = await _seed_queued_video(
        storage_client, coach_email="hb-coach@example.com"
    )
    claimed = await claim_job(db_session, job_id=job_id, celery_task_id="t-hb")
    assert claimed is not None
    first_hb = claimed.heartbeat_at

    await touch_heartbeat(db_session, job_id=job_id, progress_percent=42)
    updated = await _load_job(db_session, job_id)
    assert updated.progress_percent == 42
    assert updated.heartbeat_at is not None
    assert first_hb is None or updated.heartbeat_at >= first_hb


@pytest.mark.asyncio(loop_scope="session")
async def test_fail_job_marks_terminal_and_is_idempotent(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team_id, _, job_id = await _seed_queued_video(
        storage_client, coach_email="fail-coach@example.com"
    )
    await claim_job(db_session, job_id=job_id, celery_task_id="t-fail")
    first = await fail_job(db_session, job_id=job_id, error_code="oops", error_message="bad")
    assert first is not None
    assert first.status is ProcessingJobStatus.FAILED
    # Second fail_job is a no-op (not PENDING/RUNNING anymore)
    second = await fail_job(db_session, job_id=job_id, error_code="oops2", error_message="nope")
    assert second is None


# ---- execute_transcode ---------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_transcode_happy_path(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="happy-coach@example.com"
    )

    result = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="celery-abc",
        request_id="celery-abc",
        storage=fake_storage,
    )
    assert result.status == "completed"
    assert result.retryable is False

    job = await _load_job(db_session, job_id)
    assert job.status is ProcessingJobStatus.COMPLETED
    assert job.progress_percent == 100
    assert job.completed_at is not None
    assert job.result_metadata is not None
    verification = job.result_metadata["verification"]
    assert verification["content_length"] == 250 * 1024 * 1024
    assert verification["client_checksum_present"] is True
    assert job.result_metadata["transcode_mode"] == "test-stub"
    assert job.result_metadata["output_sha256"] == "b" * 64
    assert job.result_metadata["outputs"]["mezzanine"].startswith(f"mezzanine/{team_id}/")

    video = await _load_video(db_session, video_id)
    assert video.status is VideoStatus.PROCESSED
    assert video.storage_key_mezzanine is not None
    assert video.storage_key_mezzanine != video.storage_key_raw
    assert video.storage_output_sha256 == "b" * 64
    assert video.raw_retention_expires_at is not None
    assert video.uploaded_by is not None

    # Audit lifecycle: STARTED + COMPLETED must both be present for the team.
    assert (
        await _count_actions(
            db_session, team_id=team_id, action=AuditAction.VIDEO_PROCESSING_STARTED
        )
        == 1
    )
    await set_worker_operator_role(db_session)
    completed_audit = await db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.team_id == team_id,
            AuditLog.action == AuditAction.VIDEO_PROCESSING_COMPLETED,
        )
        .order_by(AuditLog.created_at.desc())
    )
    await clear_worker_context(db_session)
    assert completed_audit is not None
    assert completed_audit.extra is not None
    assert completed_audit.extra["originating_user_id"] == str(video.uploaded_by)


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_transcode_skips_late_completion_after_processing_cancel(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="cancel-race-worker@example.com"
    )

    async def _cancel_during_transcode(
        *,
        video: Video,
        presigner: StoragePresigner,
        settings: Settings,
    ) -> BrowserMezzanineArtifact:
        mezzanine_key = storage_key_for_mezzanine(
            team_id=str(video.team_id),
            video_id=str(video.id),
        )
        fake_storage.object_sizes[mezzanine_key] = 123
        fake_storage.object_metadata[mezzanine_key] = {"nbu-output-sha256": "c" * 64}
        await set_worker_context(db_session, team_id=team_id)
        await db_session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(
                status=ProcessingJobStatus.FAILED,
                completed_at=datetime.now(UTC),
                error_message=f"[{ErrorCode.PROCESSING_CANCELLED}] cancelled",
            )
        )
        await db_session.execute(
            update(Video).where(Video.id == video_id).values(status=VideoStatus.FAILED)
        )
        await db_session.commit()
        return BrowserMezzanineArtifact(
            mezzanine_key=mezzanine_key,
            storage_etag="c" * 32,
            output_sha256="c" * 64,
            output_size_bytes=123,
            duration_seconds=10.0,
            width=1280,
            height=720,
            fps=30.0,
            codec="h264",
            transcoder="test-cancel-race",
        )

    monkeypatch.setattr(
        "nextballup_worker.runtime.transcode.create_browser_mezzanine",
        _cancel_during_transcode,
    )

    result = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="old-transcode-task",
        request_id="old-transcode-task",
        storage=fake_storage,
    )

    assert result.status == "skipped"
    assert result.error_code == ErrorCode.PROCESSING_CANCELLED
    job = await _load_job(db_session, job_id)
    video = await _load_video(db_session, video_id)
    assert job.status is ProcessingJobStatus.FAILED
    assert video.status is VideoStatus.FAILED
    assert video.storage_key_mezzanine is None
    assert storage_key_for_mezzanine(team_id=str(team_id), video_id=str(video_id)) not in (
        fake_storage.object_sizes
    )
    assert (
        await _count_actions(
            db_session,
            team_id=team_id,
            action=AuditAction.VIDEO_PROCESSING_COMPLETED,
        )
        == 0
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_transcode_runtime_role_rebinds_rls_after_internal_commits(
    db_session: AsyncSession,
    engine: AsyncEngine,
    fake_storage: FakeWorkerStorage,
) -> None:
    password = "RuntimeWorkerAuditTest1"
    owner_url = os.environ["DATABASE_URL"]
    runtime_url = owner_url.replace(
        "nextballup:nextballup_dev@",
        f"nextballup_app:{password}@",
    )
    async with engine.begin() as connection:
        await _set_runtime_role_password(connection, "nextballup_app", password)

    team_id, video_id, job_id = await _seed_committed_worker_video(engine, fake_storage)
    runtime_engine = create_async_engine(runtime_url, poolclass=NullPool)
    try:
        runtime_sessionmaker = async_sessionmaker(
            runtime_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with runtime_sessionmaker() as runtime_session:
            result = await execute_transcode(
                runtime_session,
                job_id=job_id,
                celery_task_id="runtime-worker-audit",
                request_id="runtime-worker-audit",
                storage=fake_storage,
            )
    finally:
        await runtime_engine.dispose()

    assert result.status == "completed"
    video = await _load_video(db_session, video_id)
    assert video.status is VideoStatus.PROCESSED
    assert (
        await _count_actions(
            db_session,
            team_id=team_id,
            action=AuditAction.VIDEO_PROCESSING_STARTED,
        )
        == 1
    )
    assert (
        await _count_actions(
            db_session,
            team_id=team_id,
            action=AuditAction.VIDEO_PROCESSING_COMPLETED,
        )
        == 1
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_transcode_legacy_video_without_uploader_still_audits(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="legacy-no-uploader-coach@example.com"
    )
    await set_worker_operator_role(db_session)
    await db_session.execute(update(Video).where(Video.id == video_id).values(uploaded_by=None))
    await db_session.commit()
    await clear_worker_context(db_session)

    result = await execute_transcode(db_session, job_id=job_id, storage=fake_storage)

    assert result.status == "completed"
    await set_worker_operator_role(db_session)
    completed_audit = await db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.team_id == team_id,
            AuditLog.action == AuditAction.VIDEO_PROCESSING_COMPLETED,
        )
        .order_by(AuditLog.created_at.desc())
    )
    await clear_worker_context(db_session)
    assert completed_audit is not None
    assert completed_audit.extra is not None
    assert "originating_user_id" not in completed_audit.extra
    assert (
        await _count_actions(
            db_session, team_id=team_id, action=AuditAction.VIDEO_PROCESSING_COMPLETED
        )
        == 1
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_transcode_queues_detection_when_cv_pipeline_enabled(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="cv-queue-coach@example.com"
    )
    settings = get_settings().model_copy(update={"cv_pipeline_enabled": True})

    result = await execute_transcode(
        db_session,
        job_id=job_id,
        storage=fake_storage,
        settings=settings,
    )
    assert result.status == "completed"

    detection = await db_session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.video_id == video_id,
            ProcessingJob.stage == ProcessingJobStage.DETECTION,
        )
    )
    assert detection is not None
    assert detection.team_id == team_id
    assert detection.status is ProcessingJobStatus.PENDING


@pytest.mark.asyncio(loop_scope="session")
async def test_cv_stage_fails_closed_without_active_model_artifact(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    _, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="cv-artifact-required@example.com"
    )
    settings = get_settings().model_copy(update={"cv_pipeline_enabled": True})
    await execute_transcode(db_session, job_id=job_id, storage=fake_storage, settings=settings)
    detection = await db_session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.video_id == video_id,
            ProcessingJob.stage == ProcessingJobStage.DETECTION,
        )
    )
    assert detection is not None

    result = await execute_cv_stage(db_session, job_id=detection.id, settings=settings)

    assert result.status == "failed"
    assert result.error_code == ErrorCode.CV_MODEL_ARTIFACT_REQUIRED
    failed = await _load_job(db_session, detection.id)
    assert failed.status is ProcessingJobStatus.FAILED
    video = await _load_video(db_session, video_id)
    assert video.status is VideoStatus.PROCESSED


@pytest.mark.asyncio(loop_scope="session")
async def test_cv_stage_contract_mode_preserves_shot_clock_optionality(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("cv-shot-clock@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (
        await storage_client.post(f"{API}/games", json=_shot_clock_game_body(team["id"]))
    ).json()
    upload = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()
    complete = (
        await storage_client.post(
            f"{API}/videos/{upload['id']}/complete",
            json={"checksum_sha256": "a" * 64},
        )
    ).json()
    video_id = uuid.UUID(complete["id"])
    settings = get_settings().model_copy(
        update={"cv_pipeline_enabled": True, "cv_require_model_artifacts": False}
    )
    await execute_transcode(
        db_session,
        job_id=uuid.UUID(complete["job_id"]),
        storage=fake_storage,
        settings=settings,
    )
    detection = await db_session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.video_id == video_id,
            ProcessingJob.stage == ProcessingJobStage.DETECTION,
        )
    )
    assert detection is not None

    result = await execute_cv_stage(db_session, job_id=detection.id, settings=settings)
    assert result.status == "completed"

    completed = await _load_job(db_session, detection.id)
    assert completed.result_metadata is not None
    assert completed.result_metadata["contract_only"] is True
    assert completed.result_metadata["shot_clock"] == {"enabled": True, "seconds": 30}
    tracking = await db_session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.video_id == video_id,
            ProcessingJob.stage == ProcessingJobStage.TRACKING,
        )
    )
    assert tracking is not None
    assert tracking.status is ProcessingJobStatus.PENDING


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_transcode_idempotent_on_duplicate_delivery(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="idem-worker-coach@example.com"
    )
    first = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="first",
        storage=fake_storage,
    )
    assert first.status == "completed"
    second = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="second",
        storage=fake_storage,
    )
    assert second.status == "skipped"
    assert second.retryable is False

    # Only one STARTED + one COMPLETED audit row, not two
    assert (
        await _count_actions(
            db_session, team_id=team_id, action=AuditAction.VIDEO_PROCESSING_STARTED
        )
        == 1
    )
    assert (
        await _count_actions(
            db_session, team_id=team_id, action=AuditAction.VIDEO_PROCESSING_COMPLETED
        )
        == 1
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_transcode_permanent_error_when_object_missing(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="missing-coach@example.com"
    )
    video = await _load_video(db_session, video_id)
    assert video.storage_key_raw is not None
    # Simulate the object being deleted between API /complete and worker run
    fake_storage.drop_keys.add(video.storage_key_raw)

    result = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="perm-fail",
        storage=fake_storage,
    )
    assert result.status == "failed"
    assert result.retryable is False
    assert result.error_code == ErrorCode.PROCESSING_OBJECT_MISSING

    job = await _load_job(db_session, job_id)
    assert job.status is ProcessingJobStatus.FAILED
    assert job.error_message is not None
    assert ErrorCode.PROCESSING_OBJECT_MISSING in job.error_message

    updated_video = await _load_video(db_session, video_id)
    assert updated_video.status is VideoStatus.FAILED

    assert (
        await _count_actions(
            db_session, team_id=team_id, action=AuditAction.VIDEO_PROCESSING_FAILED
        )
        == 1
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_transcode_size_mismatch_is_permanent(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="sizemis-coach@example.com"
    )
    video = await _load_video(db_session, video_id)
    assert video.storage_key_raw is not None
    # Flip the reported size so verification trips the mismatch branch
    fake_storage.object_sizes[video.storage_key_raw] = 1  # was 250 MiB

    result = await execute_transcode(
        db_session,
        job_id=job_id,
        storage=fake_storage,
    )
    assert result.status == "failed"
    assert result.retryable is False
    assert result.error_code == ErrorCode.PROCESSING_SIZE_MISMATCH


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_transcode_content_type_mismatch_is_permanent_and_audited(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="content-type-mismatch-coach@example.com"
    )
    await set_worker_operator_role(db_session)
    await db_session.execute(update(Video).where(Video.id == video_id).values(checksum_sha256=None))
    await db_session.commit()
    await clear_worker_context(db_session)
    fake_storage.download_payload = _PNG_PAYLOAD
    monkeypatch.setattr(
        "nextballup_worker.runtime.transcode.create_browser_mezzanine",
        real_create_browser_mezzanine,
    )

    result = await execute_transcode(
        db_session,
        job_id=job_id,
        storage=fake_storage,
    )

    assert result.status == "failed"
    assert result.retryable is False
    assert result.error_code == ErrorCode.PROCESSING_CONTENT_TYPE_MISMATCH
    job = await _load_job(db_session, job_id)
    assert job.status is ProcessingJobStatus.FAILED
    video = await _load_video(db_session, video_id)
    assert video.status is VideoStatus.FAILED
    audit = await db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.team_id == team_id,
            AuditLog.action == AuditAction.VIDEO_PROCESSING_FAILED,
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.extra is not None
    assert audit.extra["declared_content_type"] == "video/mp4"
    assert audit.extra["detected_content_type"] == "image/png"


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_transcode_transient_error_leaves_job_retryable(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="trans-coach@example.com"
    )
    video = await _load_video(db_session, video_id)
    assert video.storage_key_raw is not None
    fake_storage.head_fail_keys.add(video.storage_key_raw)

    result = await execute_transcode(
        db_session,
        job_id=job_id,
        storage=fake_storage,
    )
    assert result.status == "failed"
    assert result.retryable is True
    assert result.error_code == ErrorCode.PROCESSING_STORAGE_FAILURE

    job = await _load_job(db_session, job_id)
    assert job.status is ProcessingJobStatus.PENDING
    assert (job.result_metadata or {}).get("attempt") == 1
    assert (job.result_metadata or {}).get(
        "last_error_code"
    ) == ErrorCode.PROCESSING_STORAGE_FAILURE
    assert job.error_message is None

    video = await _load_video(db_session, video_id)
    assert video.status is VideoStatus.QUEUED

    assert (
        await _count_actions(
            db_session, team_id=team_id, action=AuditAction.VIDEO_PROCESSING_FAILED
        )
        == 1
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_transcode_raises_for_unexpected_stage(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_id, _, job_id = await _seed_queued_video(
        storage_client, coach_email="badstage-coach@example.com"
    )
    # Pretend a downstream stage job was scheduled; Phase 4 only runs transcode
    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .values(stage=ProcessingJobStage.DETECTION)
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    with pytest.raises(PermanentProcessingError):
        await execute_transcode(
            db_session,
            job_id=job_id,
            storage=fake_storage,
        )


# ---- recover_stale_jobs --------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_recover_stale_jobs_marks_failed(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="stale-coach@example.com"
    )
    await claim_job(db_session, job_id=job_id, celery_task_id="stale-task")

    stale_time = datetime.now(tz=UTC) - timedelta(seconds=600)
    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(ProcessingJob).where(ProcessingJob.id == job_id).values(heartbeat_at=stale_time)
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    recovered = await recover_stale_jobs(db_session)
    assert str(job_id) in recovered

    job = await _load_job(db_session, job_id)
    assert job.status is ProcessingJobStatus.FAILED
    video = await _load_video(db_session, video_id)
    assert video.status is VideoStatus.FAILED

    assert (
        await _count_actions(
            db_session,
            team_id=team_id,
            action=AuditAction.VIDEO_PROCESSING_RECOVERED_STALE,
        )
        == 1
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_recover_stale_jobs_skips_fresh_heartbeat(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team_id, _, job_id = await _seed_queued_video(
        storage_client, coach_email="fresh-coach@example.com"
    )
    claimed = await claim_job(db_session, job_id=job_id, celery_task_id="fresh-task")
    assert claimed is not None

    recovered = await recover_stale_jobs(db_session)
    assert str(job_id) not in recovered

    job = await _load_job(db_session, job_id)
    assert job.status is ProcessingJobStatus.RUNNING


# ---- cleanup_abandoned_uploads -------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_abandoned_uploads_aborts_multipart_and_marks_failed(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    coach_email = "abandon-coach@example.com"
    await storage_client.post(f"{API}/auth/register", json=_coach(coach_email))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    initiate = (
        await storage_client.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"], file_size_bytes=2 * 1024 * 1024 * 1024),
        )
    ).json()
    video_id = uuid.UUID(initiate["id"])

    # Force the upload to look abandoned
    past = datetime.now(tz=UTC) - timedelta(hours=2)
    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(Video).where(Video.id == video_id).values(upload_expires_at=past)
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    video_before = await _load_video(db_session, video_id)
    assert video_before.status is VideoStatus.PENDING_UPLOAD
    # Capture the upload_id before cleanup runs; the shared test session's
    # identity map reflects the post-UPDATE attribute once cleanup sets it to
    # None, so we can't read it off `video_before` afterwards.
    original_upload_id = video_before.upload_id
    assert original_upload_id is not None

    abandoned = await cleanup_abandoned_uploads(
        db_session,
        storage=fake_storage,
    )
    assert str(video_id) in abandoned

    video_after = await _load_video(db_session, video_id)
    assert video_after.status is VideoStatus.FAILED
    assert video_after.upload_id is None
    usage_sum = await db_session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.team_id == uuid.UUID(team["id"]),
            UsageEvent.event_key == "video.upload.initiated",
        )
    )
    assert usage_sum == 0

    assert fake_storage.aborted_multiparts, "abort_multipart was not invoked"
    assert fake_storage.aborted_multiparts[-1]["upload_id"] == original_upload_id

    assert (
        await _count_actions(
            db_session,
            team_id=uuid.UUID(team["id"]),
            action=AuditAction.VIDEO_UPLOAD_ABANDONED,
        )
        == 1
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_abandoned_uploads_marks_failed_when_multipart_abort_fails(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("abort-fail@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()
    initiate = (
        await storage_client.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"], file_size_bytes=2 * 1024 * 1024 * 1024),
        )
    ).json()
    video_id = uuid.UUID(initiate["id"])
    video_before = await _load_video(db_session, video_id)
    assert video_before.upload_id is not None
    fake_storage.abort_fail_upload_ids.add(video_before.upload_id)

    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(Video)
        .where(Video.id == video_id)
        .values(upload_expires_at=datetime.now(tz=UTC) - timedelta(hours=2))
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    abandoned = await cleanup_abandoned_uploads(db_session, storage=fake_storage)

    assert str(video_id) in abandoned
    video_after = await _load_video(db_session, video_id)
    assert video_after.status is VideoStatus.FAILED
    assert video_after.upload_id is None


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_abandoned_uploads_skips_fresh_windows(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("fresh-upload@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()
    initiate = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()

    abandoned = await cleanup_abandoned_uploads(
        db_session,
        storage=fake_storage,
    )
    assert initiate["id"] not in abandoned
    assert fake_storage.aborted_multiparts == []


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_expired_raw_videos_deletes_terminal_source_object(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="raw-retention-coach@example.com"
    )
    result = await execute_transcode(db_session, job_id=job_id, storage=fake_storage)
    assert result.status == "completed"

    video_before = await _load_video(db_session, video_id)
    raw_key = video_before.storage_key_raw
    assert raw_key is not None
    assert raw_key in fake_storage.object_sizes

    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(Video)
        .where(Video.id == video_id)
        .values(raw_retention_expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    deleted = await cleanup_expired_raw_videos(db_session, storage=fake_storage)
    assert str(video_id) in deleted
    assert raw_key not in fake_storage.object_sizes

    video_after = await _load_video(db_session, video_id)
    assert video_after.raw_delete_requested_at is not None
    assert video_after.raw_storage_deleted_at is not None
    assert video_after.raw_deleted_at is not None
    assert video_after.raw_delete_reason == "retention_expired"
    assert video_after.storage_key_raw is None
    assert video_after.storage_etag is None
    assert (
        await _count_actions(
            db_session,
            team_id=team_id,
            action=AuditAction.VIDEO_RAW_OBJECT_DELETED,
        )
        == 1
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_expired_raw_videos_retries_when_storage_delete_fails(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="raw-delete-failure-coach@example.com"
    )
    result = await execute_transcode(db_session, job_id=job_id, storage=fake_storage)
    assert result.status == "completed"

    video_before = await _load_video(db_session, video_id)
    raw_key = video_before.storage_key_raw
    assert raw_key is not None
    fake_storage.delete_fail_keys.add(raw_key)

    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(Video)
        .where(Video.id == video_id)
        .values(raw_retention_expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    first = await cleanup_expired_raw_videos(db_session, storage=fake_storage)
    assert str(video_id) not in first
    assert raw_key in fake_storage.object_sizes
    video_after_failure = await _load_video(db_session, video_id)
    assert video_after_failure.raw_delete_requested_at is not None
    assert video_after_failure.raw_delete_failed_at is not None
    assert video_after_failure.raw_storage_deleted_at is None
    assert video_after_failure.raw_deleted_at is None
    assert video_after_failure.raw_delete_reason == "retention_expired"
    assert video_after_failure.storage_key_raw == raw_key
    assert (
        await _count_actions(
            db_session,
            team_id=team_id,
            action=AuditAction.VIDEO_RAW_OBJECT_DELETE_FAILED,
        )
        == 1
    )

    fake_storage.delete_fail_keys.remove(raw_key)
    second = await retry_raw_video_storage_deletes(db_session, storage=fake_storage)
    assert str(video_id) in second
    assert raw_key not in fake_storage.object_sizes
    video_after_retry = await _load_video(db_session, video_id)
    assert video_after_retry.raw_delete_requested_at is not None
    assert video_after_retry.raw_storage_deleted_at is not None
    assert video_after_retry.raw_deleted_at is not None
    assert video_after_retry.raw_delete_failed_at is None
    assert video_after_retry.raw_delete_reason == "retention_expired"
    assert video_after_retry.storage_key_raw is None
    assert video_after_retry.storage_etag is None
    assert (
        await _count_actions(
            db_session,
            team_id=team_id,
            action=AuditAction.VIDEO_RAW_OBJECT_DELETED,
        )
        == 1
    )

    third = await retry_raw_video_storage_deletes(db_session, storage=fake_storage)
    assert str(video_id) not in third
    assert (
        await _count_actions(
            db_session,
            team_id=team_id,
            action=AuditAction.VIDEO_RAW_OBJECT_DELETED,
        )
        == 1
    )


# ---- dispatch_pending_jobs ----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_dispatch_pending_jobs_persists_task_id_and_audits(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team_id, _, job_id = await _seed_queued_video(
        storage_client, coach_email="dispatch-coach@example.com"
    )

    # Force the PENDING job to be old enough to satisfy the grace window
    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .values(created_at=datetime.now(tz=UTC) - timedelta(seconds=60))
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    calls: list[tuple[uuid.UUID, ProcessingJobStage]] = []

    def _enqueue(pending_id: uuid.UUID, stage: ProcessingJobStage) -> str:
        calls.append((pending_id, stage))
        return f"celery-{pending_id.hex[:8]}"

    dispatched = await dispatch_pending_jobs(db_session, enqueue=_enqueue)
    assert str(job_id) in dispatched
    assert calls and calls[0][0] == job_id
    assert calls[0][1] is ProcessingJobStage.TRANSCODE

    job = await _load_job(db_session, job_id)
    assert job.celery_task_id is not None
    assert job.celery_task_id.startswith("celery-")

    assert (
        await _count_actions(
            db_session,
            team_id=team_id,
            action=AuditAction.VIDEO_PROCESSING_DISPATCHED,
        )
        == 1
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_dispatch_skips_too_recent_jobs(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team_id, _, job_id = await _seed_queued_video(
        storage_client, coach_email="fresh-job@example.com"
    )

    called = False

    def _enqueue(pending_id: uuid.UUID, stage: ProcessingJobStage) -> str:
        nonlocal called
        called = True
        return "never"

    # Job was just created; grace window excludes it
    dispatched = await dispatch_pending_jobs(db_session, enqueue=_enqueue)
    assert str(job_id) not in dispatched
    assert called is False


@pytest.mark.asyncio(loop_scope="session")
async def test_dispatch_skips_already_dispatched_jobs(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team_id, _, job_id = await _seed_queued_video(
        storage_client, coach_email="dup-dispatch@example.com"
    )
    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .values(
            created_at=datetime.now(tz=UTC) - timedelta(seconds=60),
            celery_task_id="existing-task",
        )
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    calls: list[uuid.UUID] = []

    def _enqueue(pending_id: uuid.UUID, stage: ProcessingJobStage) -> str:
        calls.append(pending_id)
        return "should-not-happen"

    dispatched = await dispatch_pending_jobs(db_session, enqueue=_enqueue)
    assert str(job_id) not in dispatched
    assert calls == []


# ---- tenant isolation ----------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_worker_audit_rows_are_tenant_scoped(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_a, video_a, job_a = await _seed_queued_video(
        storage_client, coach_email="tenant-a-coach@example.com"
    )
    team_b, video_b, job_b = await _seed_queued_video(
        storage_client, coach_email="tenant-b-coach@example.com"
    )

    await execute_transcode(db_session, job_id=job_a, storage=fake_storage)
    await execute_transcode(db_session, job_id=job_b, storage=fake_storage)

    # Each STARTED/COMPLETED audit row must reference the right team
    await set_worker_operator_role(db_session)
    rows = await db_session.execute(
        select(AuditLog.action, AuditLog.team_id, AuditLog.resource_id).where(
            AuditLog.action.in_(
                [
                    AuditAction.VIDEO_PROCESSING_STARTED,
                    AuditAction.VIDEO_PROCESSING_COMPLETED,
                ]
            ),
            AuditLog.team_id.in_([team_a, team_b]),
        )
    )
    records = rows.all()
    await clear_worker_context(db_session)

    by_team: dict[uuid.UUID, list[str]] = {row.team_id: [] for row in records}
    for row in records:
        by_team[row.team_id].append(row.action)
    assert AuditAction.VIDEO_PROCESSING_STARTED in by_team.get(team_a, [])
    assert AuditAction.VIDEO_PROCESSING_COMPLETED in by_team.get(team_a, [])
    assert AuditAction.VIDEO_PROCESSING_STARTED in by_team.get(team_b, [])
    assert AuditAction.VIDEO_PROCESSING_COMPLETED in by_team.get(team_b, [])


# ---- API status endpoint reflects worker state ---------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_status_endpoint_reflects_completed_worker_run(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="status-coach@example.com"
    )

    result = await execute_transcode(
        db_session,
        job_id=job_id,
        storage=fake_storage,
    )
    assert result.status == "completed"

    status_response = await storage_client.get(f"{API}/videos/{video_id}/status")
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == VideoStatus.PROCESSED.value
    assert body["stages"]["transcode"]["status"] == ProcessingJobStatus.COMPLETED.value
    # Other stages are still pending (Phase 4 only runs transcode)
    for stage_name in ("detection", "tracking", "court_mapping", "events", "metrics"):
        assert body["stages"][stage_name]["status"] == "pending"


# ---- startup validation ---------------------------------------------------


def test_worker_startup_requires_broker_outside_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    # Settings also read `.env`, so deleting the process env alone is not
    # enough on local machines that already have a broker configured there.
    monkeypatch.setenv("CELERY_BROKER_URL", "")
    reload_settings()
    try:
        with pytest.raises(RuntimeError, match="CELERY_BROKER_URL must be configured"):
            _ensure_runtime_broker_configured()
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        reload_settings()


def test_worker_startup_requires_runtime_db_role_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("DATABASE_URL_RUNTIME", "")
    reload_settings()
    try:
        with pytest.raises(RuntimeError, match="DATABASE_URL_RUNTIME or DATABASE_RUNTIME_PASSWORD"):
            _ensure_runtime_broker_configured()
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.delenv("DATABASE_URL_RUNTIME", raising=False)
        reload_settings()


def test_worker_startup_requires_media_sandbox_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
    monkeypatch.setenv(
        "DATABASE_URL_RUNTIME",
        "postgresql+asyncpg://nextballup_app:nextballup_app_pw@localhost:5432/nextballup",
    )
    monkeypatch.setenv("WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED", "false")
    monkeypatch.setenv("WORKER_MEDIA_MAX_CPU_SECONDS", "7200")
    monkeypatch.setenv("WORKER_MEDIA_MAX_OUTPUT_BYTES", "107374182400")
    reload_settings()
    try:
        with pytest.raises(
            RuntimeError,
            match="WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED must be true",
        ):
            _ensure_runtime_broker_configured()
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.delenv("DATABASE_URL_RUNTIME", raising=False)
        monkeypatch.delenv("WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED", raising=False)
        monkeypatch.delenv("WORKER_MEDIA_MAX_CPU_SECONDS", raising=False)
        monkeypatch.delenv("WORKER_MEDIA_MAX_OUTPUT_BYTES", raising=False)
        reload_settings()


def test_worker_startup_accepts_render_alpha_subprocess_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
    monkeypatch.setenv(
        "DATABASE_URL_RUNTIME",
        "postgresql+asyncpg://nextballup_app:nextballup_app_pw@localhost:5432/nextballup",
    )
    monkeypatch.setenv("WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED", "false")
    monkeypatch.setenv("WORKER_MEDIA_SUBPROCESS_SANDBOX", "true")
    monkeypatch.setenv("WORKER_MEDIA_MAX_CPU_SECONDS", "7200")
    monkeypatch.setenv("WORKER_MEDIA_MAX_OUTPUT_BYTES", "107374182400")
    reload_settings()
    try:
        _ensure_runtime_broker_configured()
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.delenv("DATABASE_URL_RUNTIME", raising=False)
        monkeypatch.delenv("WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED", raising=False)
        monkeypatch.delenv("WORKER_MEDIA_SUBPROCESS_SANDBOX", raising=False)
        monkeypatch.delenv("WORKER_MEDIA_MAX_CPU_SECONDS", raising=False)
        monkeypatch.delenv("WORKER_MEDIA_MAX_OUTPUT_BYTES", raising=False)
        reload_settings()


def test_worker_startup_requires_some_media_sandbox_in_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
    monkeypatch.setenv(
        "DATABASE_URL_RUNTIME",
        "postgresql+asyncpg://nextballup_app:nextballup_app_pw@localhost:5432/nextballup",
    )
    monkeypatch.setenv("WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED", "false")
    monkeypatch.setenv("WORKER_MEDIA_SUBPROCESS_SANDBOX", "false")
    monkeypatch.setenv("WORKER_MEDIA_MAX_CPU_SECONDS", "7200")
    monkeypatch.setenv("WORKER_MEDIA_MAX_OUTPUT_BYTES", "107374182400")
    reload_settings()
    try:
        with pytest.raises(
            RuntimeError,
            match="WORKER_MEDIA_SUBPROCESS_SANDBOX or WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED",
        ):
            _ensure_runtime_broker_configured()
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.delenv("DATABASE_URL_RUNTIME", raising=False)
        monkeypatch.delenv("WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED", raising=False)
        monkeypatch.delenv("WORKER_MEDIA_SUBPROCESS_SANDBOX", raising=False)
        monkeypatch.delenv("WORKER_MEDIA_MAX_CPU_SECONDS", raising=False)
        monkeypatch.delenv("WORKER_MEDIA_MAX_OUTPUT_BYTES", raising=False)
        reload_settings()


def test_worker_startup_requires_demo_preview_dependencies_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    training_root = tmp_path / "training"
    training_root.mkdir()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("CV_DEMO_PREVIEW_ENABLED", "true")
    monkeypatch.setenv("CV_DEMO_TRAINING_REPO_ROOT", str(training_root))
    monkeypatch.setenv("CV_DEMO_CONFIG_PATH", str(training_root / "missing.yaml"))
    monkeypatch.setenv("CV_DEMO_CHECKPOINT_PATH", str(training_root / "missing.pth"))
    reload_settings()
    try:
        with pytest.raises(RuntimeError, match="dependencies are not available"):
            _ensure_runtime_broker_configured()
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.delenv("CV_DEMO_PREVIEW_ENABLED", raising=False)
        monkeypatch.delenv("CV_DEMO_TRAINING_REPO_ROOT", raising=False)
        monkeypatch.delenv("CV_DEMO_CONFIG_PATH", raising=False)
        monkeypatch.delenv("CV_DEMO_CHECKPOINT_PATH", raising=False)
        reload_settings()


@pytest.mark.asyncio(loop_scope="session")
async def test_worker_session_uses_runtime_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nextballup_worker import session as worker_session_module

    created_urls: list[str] = []

    class FakeEngine:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    fake_session = cast("AsyncSession", object())

    class FakeSessionContext:
        async def __aenter__(self) -> AsyncSession:
            return fake_session

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

    class FakeSessionMaker:
        def __call__(self) -> FakeSessionContext:
            return FakeSessionContext()

    fake_engine = FakeEngine()

    def fake_create_async_engine(url: str, **kwargs: object) -> FakeEngine:
        created_urls.append(url)
        return fake_engine

    def fake_async_sessionmaker(*args: object, **kwargs: object) -> FakeSessionMaker:
        return FakeSessionMaker()

    monkeypatch.setattr(worker_session_module, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(worker_session_module, "async_sessionmaker", fake_async_sessionmaker)

    settings = Settings(
        app_env="development",
        database_url="postgresql+asyncpg://owner@localhost:5432/nextballup",
        database_url_sync="postgresql://owner@localhost:5432/nextballup",
        database_url_runtime="postgresql+asyncpg://app@localhost:5432/nextballup",
        jwt_private_key="test-private-key",
        jwt_public_key="test-public-key",
    )

    async with worker_session_module.worker_session(settings) as session:
        assert session is fake_session

    assert created_urls == [settings.database_url_runtime]
    assert fake_engine.disposed is True


# ---- Dead-letter simulation ---------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_dead_letter_marks_terminal_failed_after_manual_fail(
    storage_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Approximates the Celery on_failure dead-letter path by directly calling
    fail_job after the per-retry transient branches are exhausted. The task
    shim's on_failure hook uses exactly this pattern."""
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="deadletter-coach@example.com"
    )
    await claim_job(db_session, job_id=job_id, celery_task_id="t-dl-1")

    # Simulate two transient failures by bumping result_metadata
    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .values(
            result_metadata={
                "attempt": 3,
                "last_error": "boom",
                "last_error_code": ErrorCode.PROCESSING_STORAGE_FAILURE,
            }
        )
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    terminal = await fail_job(
        db_session,
        job_id=job_id,
        error_code=ErrorCode.PROCESSING_STORAGE_FAILURE,
        error_message="max retries exhausted",
        # Simulate the Celery on_failure hook preserving prior retry metadata
        result_metadata={
            "attempt": 3,
            "last_error": "boom",
            "last_error_code": ErrorCode.PROCESSING_STORAGE_FAILURE,
        },
    )
    assert terminal is not None
    assert terminal.status is ProcessingJobStatus.FAILED
    assert "max retries exhausted" in (terminal.error_message or "")
    assert (terminal.result_metadata or {}).get("attempt") == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_demo_preview_completes_and_updates_video_detail(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
    demo_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="worker-demo-preview@example.com"
    )
    transcode = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="celery-transcode-demo-preview",
        storage=fake_storage,
    )
    assert transcode.status == "completed"

    def _fake_runner(*, settings: Any, input_path: Path, output_path: Path) -> None:
        assert input_path.is_file()
        output_path.write_bytes(b"annotated-preview")

    monkeypatch.setattr("nextballup_core.demo_preview._run_demo_preview_inference", _fake_runner)

    result = await execute_demo_preview(
        db_session,
        video_id=video_id,
        celery_task_id="celery-demo-preview",
        settings=get_settings(),
        storage=fake_storage,
    )
    assert result.status == "completed"

    preview_path = demo_preview_env["preview_root"] / str(video_id) / "demo-preview.annotated.mp4"
    assert preview_path.is_file()
    assert preview_path.read_bytes() == b"annotated-preview"
    preview_key = f"artifacts/{team_id}/{video_id}/demo-preview.annotated.mp4"
    assert fake_storage.object_sizes[preview_key] == len(b"annotated-preview")

    detail = await storage_client.get(f"{API}/videos/{video_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["demo_preview_status"] == "completed"
    assert body["demo_preview_url"] == f"/api/v1/videos/{video_id}/demo-preview/artifact"
    assert body["demo_preview_generated_at"] is not None
    assert body["demo_preview_error_message"] is None

    artifact = await storage_client.get(f"{API}/videos/{video_id}/demo-preview/artifact")
    assert artifact.status_code == 307, artifact.text
    assert artifact.headers["cache-control"] == "private, no-store, max-age=0"
    assert artifact.headers["location"].startswith(f"https://fake-storage.test/{preview_key}")
    assert "exp=7200" in artifact.headers["location"]

    assert (
        await _count_actions(
            db_session,
            team_id=team_id,
            action=AuditAction.VIDEO_DEMO_PREVIEW_GENERATED,
        )
        >= 1
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_demo_preview_imports_alpha_candidate_tags(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
    demo_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="worker-demo-preview-candidates@example.com"
    )
    transcode = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="celery-transcode-demo-preview-candidates",
        storage=fake_storage,
    )
    assert transcode.status == "completed"

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CV_ALPHA_DETECTOR_PREVIEW_ENABLED", "true")
    monkeypatch.setenv("CV_ALPHA_CANDIDATE_TAGS_ENABLED", "true")
    reload_settings()
    settings = get_settings()

    preview_dir = demo_preview_env["preview_root"] / str(video_id)
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / "demo-preview.annotated.mp4"
    preview_path.write_bytes(b"annotated-preview-with-candidates")
    candidate_path = preview_dir / "alpha-candidate-tags.json"
    candidate_path.write_text(
        json.dumps(
            {
                "schema_version": "alpha_video_candidate_tags_v1",
                "source": "restricted_bard_lora_alpha_video_windows",
                "lineage": {
                    "demo_only": True,
                    "review_required": True,
                    "commercial_use_allowed": False,
                    "restricted_source": True,
                },
                "review_required": True,
                "demo_only": True,
                "commercial_use_allowed": False,
                "not_production_analytics": True,
                "candidates": [
                    {
                        "candidate_id": "alpha-video-candidate-000001",
                        "event_type": "shot_attempt",
                        "start_time_ms": 4000,
                        "end_time_ms": 12000,
                        "predicted_actions": ["3PT Shot"],
                        "review_required": True,
                        "commercial_use_allowed": False,
                    }
                ],
                "blocker": None,
            }
        ),
        encoding="utf-8",
    )

    async def _fake_render(**kwargs: Any) -> DemoPreviewArtifact:
        return DemoPreviewArtifact(
            output_path=preview_path,
            url_path=f"/api/v1/videos/{video_id}/demo-preview/artifact",
            generated_at=datetime.now(tz=UTC),
            candidate_tags_path=candidate_path,
        )

    monkeypatch.setattr(
        "nextballup_worker.runtime.demo_preview.render_demo_preview_artifact",
        _fake_render,
    )

    result = await execute_demo_preview(
        db_session,
        video_id=video_id,
        celery_task_id="celery-demo-preview-candidates",
        settings=settings,
        storage=fake_storage,
    )
    assert result.status == "completed"

    await set_worker_operator_role(db_session)
    events = (
        (await db_session.execute(select(VideoEvent).where(VideoEvent.video_id == video_id)))
        .scalars()
        .all()
    )
    event_job = await db_session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.video_id == video_id,
            ProcessingJob.stage == ProcessingJobStage.EVENTS,
        )
    )
    await clear_worker_context(db_session)

    assert event_job is not None
    assert event_job.status is ProcessingJobStatus.COMPLETED
    assert event_job.progress_percent == 100
    assert (event_job.result_metadata or {})["not_production_analytics"] is True
    assert len(events) == 1
    event = events[0]
    assert event.team_id == team_id
    assert event.event_type is VideoEventType.SHOT_ATTEMPT
    assert event.review_status is ReviewStatus.NEEDS_REVIEW
    assert event.confidence is None
    assert event.event_metadata == {
        "source": "restricted_bard_lora_alpha_video_windows",
        "candidate_id": "alpha-video-candidate-000001",
        "not_production_analytics": True,
        "review_copy": "Review required. Alpha candidate only. Not production analytics.",
        "predicted_actions": ["3PT Shot"],
        "clip_pre_ms": 4000,
        "clip_post_ms": 4000,
    }

    proposals = await storage_client.get(f"{API}/videos/{video_id}/clip-proposals")
    assert proposals.status_code == 200, proposals.text
    body = proposals.json()
    assert body["proposals"][0]["label"] == "Shot attempt"
    assert body["proposals"][0]["review_status"] == "needs_review"
    assert "confidence" not in json.dumps(body).lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_demo_preview_rejects_sensitive_alpha_candidate_report(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
    demo_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="worker-demo-preview-candidates-sensitive@example.com"
    )
    transcode = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="celery-transcode-demo-preview-sensitive-candidates",
        storage=fake_storage,
    )
    assert transcode.status == "completed"

    await set_worker_operator_role(db_session)
    db_session.add(
        VideoEvent(
            video_id=video_id,
            team_id=team_id,
            event_type=VideoEventType.PASS,
            event_time_ms=1000,
            output_frame=30,
            shot_clock_enabled=False,
            review_status=ReviewStatus.NEEDS_REVIEW,
            event_metadata={
                "source": "restricted_bard_lora_alpha_video_windows",
                "candidate_id": "stale-alpha-candidate",
            },
        )
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CV_ALPHA_DETECTOR_PREVIEW_ENABLED", "true")
    monkeypatch.setenv("CV_ALPHA_CANDIDATE_TAGS_ENABLED", "true")
    reload_settings()
    settings = get_settings()

    preview_dir = demo_preview_env["preview_root"] / str(video_id)
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / "demo-preview.annotated.mp4"
    preview_path.write_bytes(b"annotated-preview-with-sensitive-candidates")
    candidate_path = preview_dir / "alpha-candidate-tags.json"
    candidate_path.write_text(
        json.dumps(
            {
                "schema_version": "alpha_video_candidate_tags_v1",
                "source": "restricted_bard_lora_alpha_video_windows",
                "lineage": {
                    "demo_only": True,
                    "review_required": True,
                    "commercial_use_allowed": False,
                    "restricted_source": True,
                },
                "review_required": True,
                "demo_only": True,
                "commercial_use_allowed": False,
                "not_production_analytics": True,
                "debug_url": "https://storage.example.invalid/signed?X-Amz-Signature=secret",
                "candidates": [],
                "blocker": None,
            }
        ),
        encoding="utf-8",
    )

    async def _fake_render(**kwargs: Any) -> DemoPreviewArtifact:
        return DemoPreviewArtifact(
            output_path=preview_path,
            url_path=f"/api/v1/videos/{video_id}/demo-preview/artifact",
            generated_at=datetime.now(tz=UTC),
            candidate_tags_path=candidate_path,
        )

    monkeypatch.setattr(
        "nextballup_worker.runtime.demo_preview.render_demo_preview_artifact",
        _fake_render,
    )

    result = await execute_demo_preview(
        db_session,
        video_id=video_id,
        celery_task_id="celery-demo-preview-sensitive-candidates",
        settings=settings,
        storage=fake_storage,
    )
    assert result.status == "completed"

    await set_worker_operator_role(db_session)
    event_count = await db_session.scalar(
        select(func.count()).select_from(VideoEvent).where(VideoEvent.video_id == video_id)
    )
    event_job = await db_session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.video_id == video_id,
            ProcessingJob.stage == ProcessingJobStage.EVENTS,
        )
    )
    await clear_worker_context(db_session)

    assert event_count == 0
    assert event_job is not None
    assert event_job.status is ProcessingJobStatus.FAILED
    assert event_job.error_message is not None
    assert "forbidden sensitive material" in event_job.error_message


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_demo_preview_does_not_mark_running_before_lock_acquisition(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
    demo_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="worker-demo-preview-lock-order@example.com"
    )
    transcode = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="celery-transcode-demo-preview-lock-order",
        storage=fake_storage,
    )
    assert transcode.status == "completed"

    async def _check_state_before_render(**kwargs: Any) -> Any:
        state = resolve_demo_preview_state(settings=get_settings(), video_id=video_id)
        assert state.status != "running"
        raise ServiceUnavailableError(
            "synthetic preview failure",
            code=ErrorCode.DEMO_PREVIEW_FAILED,
        )

    monkeypatch.setattr(
        "nextballup_worker.runtime.demo_preview.render_demo_preview_artifact",
        _check_state_before_render,
    )

    result = await execute_demo_preview(
        db_session,
        video_id=video_id,
        celery_task_id="celery-demo-preview-lock-order",
        settings=get_settings(),
        storage=fake_storage,
    )
    assert result.status == "failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_demo_preview_in_progress_duplicate_is_skipped(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
    demo_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="worker-demo-preview-duplicate@example.com"
    )
    transcode = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="celery-transcode-demo-preview-duplicate",
        storage=fake_storage,
    )
    assert transcode.status == "completed"

    mark_demo_preview_running(
        settings=get_settings(),
        video_id=video_id,
        task_id="winning-task",
    )

    async def _raise_in_progress(**kwargs: Any) -> Any:
        raise ConflictError(
            "A local demo preview is already being generated for this video",
            code=ErrorCode.DEMO_PREVIEW_IN_PROGRESS,
        )

    monkeypatch.setattr(
        "nextballup_worker.runtime.demo_preview.render_demo_preview_artifact",
        _raise_in_progress,
    )

    result = await execute_demo_preview(
        db_session,
        video_id=video_id,
        celery_task_id="celery-demo-preview-duplicate",
        settings=get_settings(),
        storage=fake_storage,
    )
    assert result.status == "skipped"

    detail = await storage_client.get(f"{API}/videos/{video_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["demo_preview_status"] == "running"


@pytest.mark.asyncio(loop_scope="session")
async def test_finalize_demo_preview_failure_marks_state_and_audits(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeWorkerStorage,
    demo_preview_env: dict[str, Path],
) -> None:
    team_id, video_id, job_id = await _seed_queued_video(
        storage_client, coach_email="worker-demo-preview-fail@example.com"
    )
    transcode = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="celery-transcode-demo-preview-fail",
        storage=fake_storage,
    )
    assert transcode.status == "completed"

    result = await finalize_demo_preview_failure(
        db_session,
        video_id=video_id,
        celery_task_id="celery-demo-preview-fail",
        settings=get_settings(),
        error_code=ErrorCode.DEMO_PREVIEW_MACHINE_BUSY,
        error_message="demo preview retries exhausted",
    )

    assert result.status == "failed"

    detail = await storage_client.get(f"{API}/videos/{video_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["demo_preview_status"] == "failed"
    assert body["demo_preview_error_message"] == "demo preview retries exhausted"

    assert (
        await _count_actions(
            db_session,
            team_id=team_id,
            action=AuditAction.VIDEO_DEMO_PREVIEW_FAILED,
        )
        >= 1
    )


def test_cleanup_expired_demo_previews_prunes_old_preview_dirs(
    demo_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    old_video_id = uuid.uuid4()
    fresh_video_id = uuid.uuid4()

    old_dir = demo_preview_env["preview_root"] / str(old_video_id)
    old_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / "demo-preview.annotated.mp4").write_bytes(b"old-preview")

    fresh_dir = demo_preview_env["preview_root"] / str(fresh_video_id)
    fresh_dir.mkdir(parents=True, exist_ok=True)
    (fresh_dir / "demo-preview.annotated.mp4").write_bytes(b"fresh-preview")

    stale_timestamp = time.time() - (settings.cv_demo_retention_seconds + 60)
    os.utime(old_dir, (stale_timestamp, stale_timestamp))
    os.utime(old_dir / "demo-preview.annotated.mp4", (stale_timestamp, stale_timestamp))

    cleaned = cleanup_expired_demo_previews(settings=settings)

    assert str(old_video_id) in cleaned
    assert not old_dir.exists()
    assert fresh_dir.exists()


def test_resolve_demo_preview_repairs_legacy_permissions(
    demo_preview_env: dict[str, Path],
) -> None:
    settings = get_settings()
    preview_root = demo_preview_env["preview_root"]
    preview_root.mkdir(parents=True, exist_ok=True)
    preview_root.chmod(0o755)

    video_id = uuid.uuid4()
    preview_dir = preview_root / str(video_id)
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.chmod(0o755)

    preview_path = preview_dir / "demo-preview.annotated.mp4"
    preview_path.write_bytes(b"preview")
    preview_path.chmod(0o644)

    state_path = preview_dir / "demo-preview.state.json"
    state_path.write_text('{"status":"completed"}\n', encoding="utf-8")
    state_path.chmod(0o644)

    artifact = resolve_demo_preview(settings=settings, video_id=video_id)

    assert artifact is not None
    assert stat.S_IMODE(preview_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(preview_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(preview_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


# Unused local symbol suppression
_ = TransientProcessingError
