"""Worker runtime tests.

These exercise the async runtime functions directly against the test DB
session, bypassing Celery. The API-side setup is reused from test_videos so
RLS-gated INSERTs go through the audited code paths.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from nextballup_api.routers.videos import get_storage
from nextballup_api.storage import PresignedPart, PresignedUpload, StoragePresigner
from nextballup_worker.errors import (
    PermanentProcessingError,
    TransientProcessingError,
)
from nextballup_worker.runtime import (
    claim_job,
    cleanup_abandoned_uploads,
    complete_job,
    dispatch_pending_jobs,
    execute_transcode,
    fail_job,
    recover_stale_jobs,
    release_job_for_retry,
    touch_heartbeat,
)
from nextballup_worker.tasks import _ensure_runtime_broker_configured
from nextballup_worker.tenant import clear_worker_context, set_worker_operator_role
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import (
    ProcessingJobStage,
    ProcessingJobStatus,
    UploadMethod,
    VideoStatus,
)
from nextballup_core.settings import reload_settings
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.video import ProcessingJob, Video

API = "/api/v1"


# ---- Fake storage ---------------------------------------------------------


class FakeWorkerStorage:
    """Storage fake used for both API seeding and worker head_object calls."""

    def __init__(self) -> None:
        self.object_sizes: dict[str, int] = {}
        self.pending_multiparts: dict[str, tuple[str, int]] = {}
        self.aborted_multiparts: list[dict[str, str]] = []
        self.completed_multiparts: list[dict[str, Any]] = []
        # Toggle to simulate object gone missing mid-pipeline.
        self.drop_keys: set[str] = set()
        self.head_fail_keys: set[str] = set()

    def is_configured(self) -> bool:
        return True

    def presign_upload(
        self, *, key: str, content_type: str, file_size_bytes: int
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
        self.aborted_multiparts.append({"key": key, "upload_id": upload_id})
        self.pending_multiparts.pop(upload_id, None)

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
        return {"ContentLength": size, "ETag": f'"{synthetic_md5}"'}

    def presign_get(
        self, *, key: str, expires_in: int, response_content_type: str | None = None
    ) -> str:
        ct_param = f"&rct={response_content_type}" if response_content_type else ""
        return f"https://fake-storage.test/{key}?X-Get=1&exp={expires_in}{ct_param}"


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

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_storage() -> StoragePresigner:
        return fake_storage

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = _override_storage
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


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

    video = await _load_video(db_session, video_id)
    assert video.status is VideoStatus.PROCESSED

    # Audit lifecycle: STARTED + COMPLETED must both be present for the team.
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
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    reload_settings()
    try:
        with pytest.raises(RuntimeError, match="CELERY_BROKER_URL must be configured"):
            _ensure_runtime_broker_configured()
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        reload_settings()


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


# Unused local symbol suppression
_ = TransientProcessingError
