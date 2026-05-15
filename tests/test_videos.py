from __future__ import annotations

import asyncio
import csv
import io
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from nextballup_api.demo_preview import queue_demo_preview_request
from nextballup_api.routers.videos import _record_upload_failure, get_storage
from nextballup_api.security.jwt import create_access_token
from nextballup_api.security.passwords import hash_password
from nextballup_api.storage import (
    PresignedPart,
    PresignedUpload,
    StorageFailureError,
    StoragePresigner,
)
from nextballup_api.video_playback_status import derive_playback_status
from nextballup_worker.runtime import execute_transcode
from nextballup_worker.tenant import clear_worker_context, set_worker_operator_role
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.demo_preview import mark_demo_preview_queued
from nextballup_core.enums import (
    ProcessingJobStage,
    ProcessingJobStatus,
    ReviewStatus,
    UploadMethod,
    UserRole,
    VideoEventType,
    VideoStatus,
)
from nextballup_core.errors import TooManyRequestsError
from nextballup_core.settings import get_settings
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.billing import UsageEvent
from nextballup_db.models.cv import VideoEvent
from nextballup_db.models.user import User
from nextballup_db.models.video import ProcessingJob, Video

API = "/api/v1"


# ---- Fake storage ---------------------------------------------------------


class FakeStorage:
    def __init__(self) -> None:
        self.completed_multiparts: list[dict[str, Any]] = []
        self.aborted_multiparts: list[dict[str, Any]] = []
        self.object_sizes: dict[str, int] = {}
        self.object_metadata: dict[str, dict[str, str]] = {}
        self.pending_multiparts: dict[str, tuple[str, int]] = {}
        self.next_upload_id: str | None = None
        self.fail_presign = False
        self.fail_complete = False
        self.fail_head_for_keys: set[str] = set()
        self.fail_delete_for_keys: set[str] = set()

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
        if self.fail_presign:
            self.fail_presign = False
            raise StorageFailureError("Failed to presign upload URL", details={"key": key})
        if file_size_bytes <= 1_073_741_824:
            self.object_sizes[key] = file_size_bytes
            return PresignedUpload(
                method=UploadMethod.PUT,
                url=f"https://fake-storage.test/{key}?X-Test=1",
                headers={"Content-Type": content_type},
            )
        upload_id = self.next_upload_id or f"fake-upload-{uuid.uuid4().hex[:8]}"
        self.next_upload_id = None
        part_count = max(1, (file_size_bytes + 99 * 1024 * 1024) // (100 * 1024 * 1024))
        self.pending_multiparts[upload_id] = (key, file_size_bytes)
        parts = tuple(
            PresignedPart(
                part_number=i,
                url=f"https://fake-storage.test/{key}?partNumber={i}&uploadId={upload_id}",
            )
            for i in range(1, int(part_count) + 1)
        )
        return PresignedUpload(
            method=UploadMethod.MULTIPART,
            upload_id=upload_id,
            parts=parts,
            part_size_bytes=100 * 1024 * 1024,
        )

    def complete_multipart(self, *, key: str, upload_id: str, parts: list[dict[str, Any]]) -> None:
        if self.fail_complete:
            self.fail_complete = False
            raise StorageFailureError(
                "Failed to complete multipart upload",
                details={"key": key, "upload_id": upload_id},
            )
        self.completed_multiparts.append({"key": key, "upload_id": upload_id, "parts": parts})
        pending = self.pending_multiparts.pop(upload_id, None)
        if pending is not None:
            _, expected_size = pending
            self.object_sizes[key] = expected_size

    def abort_multipart(self, *, key: str, upload_id: str) -> None:
        self.aborted_multiparts.append({"key": key, "upload_id": upload_id})
        self.pending_multiparts.pop(upload_id, None)

    def delete_object(self, *, key: str) -> None:
        if key in self.fail_delete_for_keys:
            raise StorageFailureError("Simulated delete failure", details={"key": key})
        self.object_sizes.pop(key, None)

    def head_object(self, *, key: str) -> dict[str, Any] | None:
        if key in self.fail_head_for_keys:
            return None
        size = self.object_sizes.get(key)
        if size is None:
            return None
        return {"ContentLength": size, "Metadata": self.object_metadata.get(key, {})}

    def presign_get(
        self, *, key: str, expires_in: int, response_content_type: str | None = None
    ) -> str:
        ct_param = f"&rct={response_content_type}" if response_content_type else ""
        return f"https://fake-storage.test/{key}?X-Get=1&exp={expires_in}{ct_param}"

    def download_file(self, *, key: str, destination: str) -> None:
        Path(destination).write_bytes(b"fake-video")

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
async def fake_storage() -> FakeStorage:
    return FakeStorage()


@pytest_asyncio.fixture(loop_scope="session")
async def storage_client(
    db_session: AsyncSession, fake_storage: FakeStorage
) -> AsyncIterator[AsyncClient]:
    """Variant of the conftest `client` fixture that wires a fake storage so
    the upload flow exercises the real route logic without S3/MinIO."""
    from nextballup_api.deps import get_db
    from nextballup_api.main import app

    from nextballup_core.settings import reload_settings
    from tests.csrf_helper import make_csrf_mirror_hook

    reload_settings()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_get_storage() -> StoragePresigner:
        return fake_storage

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = _override_get_storage
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


@pytest_asyncio.fixture(loop_scope="session")
async def no_storage_client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """Client with storage explicitly unconfigured (Depends returns None)."""
    from nextballup_api.deps import get_db
    from nextballup_api.main import app

    from nextballup_core.settings import reload_settings
    from tests.csrf_helper import make_csrf_mirror_hook

    reload_settings()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_get_storage() -> StoragePresigner | None:
        return None

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = _override_get_storage
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


# ---- Helpers --------------------------------------------------------------


def _coach_payload(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "password": "Password1!",
        "full_name": "Mike Johnson",
        "role": "coach",
    }


def _player_payload(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "password": "Password1!",
        "full_name": "James Williams",
        "role": "player",
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


def _privacy_consent_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "label": "2026 guardian and athlete video release",
        "consent_source": "written_permission",
        "covers_video_uploads": True,
        "covers_cv_processing": True,
        "commercial_ml_training_allowed": False,
        "minors_authorized": True,
        "athlete_pii_authorized": True,
        "evidence_uri": "s3://legal-evidence/team-release-2026.pdf",
    }
    body.update(overrides)
    return body


async def _register(client: AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


async def _setup_coach_team_game(
    client: AsyncClient, *, coach_email: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    await _register(client, _coach_payload(coach_email))
    team = cast("dict[str, Any]", (await client.post(f"{API}/teams", json=_team_body())).json())
    game = cast(
        "dict[str, Any]", (await client.post(f"{API}/games", json=_game_body(team["id"]))).json()
    )
    return team, game


async def _seed_processed_video(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    *,
    coach_email: str,
) -> tuple[dict[str, Any], uuid.UUID]:
    team, game = await _setup_coach_team_game(client, coach_email=coach_email)
    upload = await client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    assert upload.status_code == 201, upload.text
    complete = await client.post(
        f"{API}/videos/{upload.json()['id']}/complete",
        json={"checksum_sha256": "d" * 64},
    )
    assert complete.status_code == 200, complete.text
    result = await execute_transcode(
        db_session,
        job_id=uuid.UUID(complete.json()["job_id"]),
        celery_task_id="celery-demo-preview",
        storage=fake_storage,
    )
    assert result.status == "completed"
    return team, uuid.UUID(complete.json()["id"])


async def _seed_admin_headers(
    db_session: AsyncSession, *, email: str = "admin-internal@example.com"
) -> dict[str, str]:
    admin = User(
        email=email,
        password_hash=hash_password("Password1!"),
        full_name="Internal Admin",
        role=UserRole.ADMIN,
    )
    db_session.add(admin)
    await db_session.flush()
    token = create_access_token(
        subject=admin.id,
        role=admin.role,
        session_version=admin.session_version,
        team_ids=[],
        settings=get_settings(),
    )
    return {"Authorization": f"Bearer {token}"}


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

    from nextballup_core.settings import reload_settings

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


@pytest.fixture()
def alpha_preview_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Path]]:
    preview_root = tmp_path / "alpha_demo_previews"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CV_DEMO_PREVIEW_ENABLED", "false")
    monkeypatch.setenv("CV_ALPHA_DETECTOR_PREVIEW_ENABLED", "true")
    monkeypatch.setenv("CV_DEMO_PREVIEW_ROOT", str(preview_root))
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")
    monkeypatch.setenv("RATE_LIMIT_FAIL_CLOSED", "false")

    from nextballup_core.settings import reload_settings

    reload_settings()
    try:
        yield {"preview_root": preview_root}
    finally:
        reload_settings()


# ---- POST /games (smoke for video parent) --------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_coach_creates_game(client: AsyncClient, db_session: AsyncSession) -> None:
    await _register(client, _coach_payload("game-coach@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_body())).json()

    response = await client.post(f"{API}/games", json=_game_body(team["id"]))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["team_id"] == team["id"]
    assert body["game_type"] == "regular_season"
    assert body["status"] == "scheduled"

    actions = await db_session.execute(
        select(AuditLog.action).where(AuditLog.action == AuditAction.GAME_CREATED)
    )
    assert AuditAction.GAME_CREATED in {row[0] for row in actions.all()}


@pytest.mark.asyncio(loop_scope="session")
async def test_create_game_persists_optional_shot_clock(
    client: AsyncClient,
) -> None:
    await _register(client, _coach_payload("game-shot-clock@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_body())).json()

    response = await client.post(f"{API}/games", json=_shot_clock_game_body(team["id"]))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["shot_clock_enabled"] is True
    assert body["shot_clock_seconds"] == 30


@pytest.mark.asyncio(loop_scope="session")
async def test_create_game_rejects_shot_clock_seconds_when_disabled(
    client: AsyncClient,
) -> None:
    await _register(client, _coach_payload("game-shot-clock-invalid@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_body())).json()
    body = _game_body(team["id"])
    body["shot_clock_enabled"] = False
    body["shot_clock_seconds"] = 30

    response = await client.post(f"{API}/games", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED


# ---- GET /games/{id}/videos ----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_game_videos_list_is_empty_before_upload(
    storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(
        storage_client, coach_email="videos-list-empty@example.com"
    )
    response = await storage_client.get(f"{API}/games/{game['id']}/videos")
    assert response.status_code == 200
    body = response.json()
    assert body == {"videos": [], "total": 0}


@pytest.mark.asyncio(loop_scope="session")
async def test_game_videos_list_returns_uploaded_videos(
    storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(
        storage_client, coach_email="videos-list-full@example.com"
    )
    created = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    assert created.status_code == 201
    response = await storage_client.get(f"{API}/games/{game['id']}/videos")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    row = body["videos"][0]
    assert row["id"] == created.json()["id"]
    assert row["filename"] == "lincoln_vs_jefferson_q1.mp4"
    assert row["status"] == "pending_upload"
    # Playback fields must not leak on the list view.
    assert "playback_url" not in row


@pytest.mark.asyncio(loop_scope="session")
async def test_game_videos_list_denies_non_members(
    storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(
        storage_client, coach_email="videos-list-owner@example.com"
    )
    await _register(storage_client, _coach_payload("videos-list-snoop@example.com"))
    response = await storage_client.get(f"{API}/games/{game['id']}/videos")
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_game_videos_list_404_for_unknown_game(
    storage_client: AsyncClient,
) -> None:
    await _setup_coach_team_game(storage_client, coach_email="videos-list-unknown@example.com")
    bogus = uuid.uuid4()
    response = await storage_client.get(f"{API}/games/{bogus}/videos")
    assert response.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_player_cannot_create_game(client: AsyncClient) -> None:
    coach_email = "owner-game@example.com"
    coach_login = _coach_payload(coach_email)
    await _register(client, coach_login)
    team = (await client.post(f"{API}/teams", json=_team_body())).json()

    await _register(client, _player_payload("player-game@example.com"))
    response = await client.post(f"{API}/games", json=_game_body(team["id"]))
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_get_game_isolation(client: AsyncClient) -> None:
    await _register(client, _coach_payload("coach-iso-a@example.com"))
    team_a = (await client.post(f"{API}/teams", json=_team_body())).json()
    game_a = (await client.post(f"{API}/games", json=_game_body(team_a["id"]))).json()

    await _register(client, _coach_payload("coach-iso-b@example.com"))
    response = await client.get(f"{API}/games/{game_a['id']}")
    # In production each request gets a fresh session and the RLS-filtered
    # lookup presents as 404. The shared-session test harness can still trip
    # the app-layer membership check first and surface 403. Either way the row
    # is not disclosed cross-tenant.
    assert response.status_code in {403, 404}


# ---- POST /videos/upload --------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_initiate_single_put_upload(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="upload-coach@example.com"
    )

    response = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["upload_method"] == "PUT"
    assert body["upload_url"].startswith("https://fake-storage.test/")
    assert body["upload_headers"] == {"Content-Type": "video/mp4"}
    assert body["upload_id"] is None
    assert body["part_urls"] is None
    video_id = body["id"]

    video = await db_session.scalar(select(Video).where(Video.id == uuid.UUID(video_id)))
    assert video is not None
    assert video.status is VideoStatus.PENDING_UPLOAD
    assert video.team_id == uuid.UUID(team["id"])
    assert video.game_id == uuid.UUID(game["id"])
    assert video.storage_key_raw and video.storage_key_raw.startswith(f"raw/{team['id']}/")

    actions = {
        row[0]
        for row in (
            await db_session.execute(
                select(AuditLog.action).where(AuditLog.team_id == uuid.UUID(team["id"]))
            )
        ).all()
    }
    assert AuditAction.VIDEO_UPLOAD_INITIATED in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_initiate_multipart_upload_above_threshold(
    storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(storage_client, coach_email="multi-coach@example.com")

    response = await storage_client.post(
        f"{API}/videos/upload",
        json=_upload_body(game["id"], file_size_bytes=2 * 1024 * 1024 * 1024),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["upload_method"] == "MULTIPART"
    assert body["upload_url"] is None
    assert body["upload_id"] is not None
    assert body["part_size_bytes"] == 100 * 1024 * 1024
    assert isinstance(body["part_urls"], list) and len(body["part_urls"]) >= 20


@pytest.mark.asyncio(loop_scope="session")
async def test_initiate_multipart_upload_accepts_r2_length_upload_id(
    storage_client: AsyncClient,
    fake_storage: FakeStorage,
    db_session: AsyncSession,
) -> None:
    _, game = await _setup_coach_team_game(
        storage_client, coach_email="r2-upload-id-coach@example.com"
    )
    r2_upload_id = "r2-" + ("A" * 350)
    fake_storage.next_upload_id = r2_upload_id

    response = await storage_client.post(
        f"{API}/videos/upload",
        json=_upload_body(game["id"], file_size_bytes=2 * 1024 * 1024 * 1024),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["upload_method"] == "MULTIPART"
    assert body["upload_id"] == r2_upload_id
    video = await db_session.scalar(select(Video).where(Video.id == uuid.UUID(body["id"])))
    assert video is not None
    assert video.upload_id == r2_upload_id


@pytest.mark.asyncio(loop_scope="session")
async def test_player_cannot_initiate_upload(storage_client: AsyncClient) -> None:
    coach_email = "owner-up@example.com"
    await _register(storage_client, _coach_payload(coach_email))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    # Player joins so they are a member but not a coach.
    await _register(storage_client, _player_payload("player-up@example.com"))
    join = await storage_client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 11},
    )
    assert join.status_code == 200, join.text

    response = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_non_member_cannot_initiate_upload(storage_client: AsyncClient) -> None:
    await _register(storage_client, _coach_payload("owner-non@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    await _register(storage_client, _coach_payload("snoop-non@example.com"))
    response = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    assert response.status_code in {403, 404}


@pytest.mark.asyncio(loop_scope="session")
async def test_initiate_rejects_unsupported_content_type(
    storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(storage_client, coach_email="ct-coach@example.com")
    response = await storage_client.post(
        f"{API}/videos/upload",
        json=_upload_body(game["id"], content_type="video/avi"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CONTENT_TYPE


@pytest.mark.asyncio(loop_scope="session")
async def test_initiate_rejects_oversize_file(storage_client: AsyncClient) -> None:
    _, game = await _setup_coach_team_game(storage_client, coach_email="big-coach@example.com")
    response = await storage_client.post(
        f"{API}/videos/upload",
        json=_upload_body(game["id"], file_size_bytes=20 * 1024 * 1024 * 1024),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.FILE_TOO_LARGE


@pytest.mark.asyncio(loop_scope="session")
async def test_initiate_rejects_undersize_file(storage_client: AsyncClient) -> None:
    """1-byte uploads are the signature of a 'mint many presigns, churn
    storage' probe. Floor should reject them before we burn an S3 call."""
    _, game = await _setup_coach_team_game(storage_client, coach_email="small-coach@example.com")
    response = await storage_client.post(
        f"{API}/videos/upload",
        json=_upload_body(game["id"], file_size_bytes=1),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.FILE_TOO_SMALL


@pytest.mark.asyncio(loop_scope="session")
async def test_initiate_rejects_content_type_extension_mismatch(
    storage_client: AsyncClient,
) -> None:
    """A `.exe` declaring `video/mp4` is a classic content-type smuggle;
    the extension check must fire before we cut a presigned URL."""
    _, game = await _setup_coach_team_game(storage_client, coach_email="mismatch-coach@example.com")
    response = await storage_client.post(
        f"{API}/videos/upload",
        json=_upload_body(
            game["id"],
            filename="malware.exe",
            content_type="video/mp4",
        ),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.CONTENT_TYPE_EXTENSION_MISMATCH


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "bad_filename",
    [
        "../escape.mp4",
        "folder/nested.mp4",
        "back\\slash.mp4",
        "null\x00byte.mp4",
        "control\x1fchar.mp4",
        " leading-space.mp4",
    ],
)
async def test_initiate_rejects_unsafe_filename(
    storage_client: AsyncClient, bad_filename: str
) -> None:
    _, game = await _setup_coach_team_game(
        storage_client, coach_email=f"bad-{abs(hash(bad_filename))}@example.com"
    )
    response = await storage_client.post(
        f"{API}/videos/upload",
        json=_upload_body(game["id"], filename=bad_filename),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.INVALID_FILENAME


@pytest.mark.asyncio(loop_scope="session")
async def test_initiate_returns_503_when_storage_not_configured(
    no_storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(
        no_storage_client, coach_email="nostore-coach@example.com"
    )
    response = await no_storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == ErrorCode.STORAGE_NOT_CONFIGURED


@pytest.mark.asyncio(loop_scope="session")
async def test_initiate_storage_failure_audits_failure(
    storage_client: AsyncClient, fake_storage: FakeStorage, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="upload-fail-coach@example.com"
    )
    fake_storage.fail_presign = True
    response = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == ErrorCode.STORAGE_FAILURE

    failed = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_UPLOAD_FAILED,
            AuditLog.team_id == uuid.UUID(team["id"]),
        )
    )
    assert failed is not None and failed >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_upload_failure_audit_rebinds_rls_context(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, _ = await _setup_coach_team_game(
        storage_client, coach_email="upload-fail-rls@example.com"
    )
    user = await db_session.scalar(select(User).where(User.email == "upload-fail-rls@example.com"))
    assert user is not None

    # Storage failures roll back the main upload transaction before auditing.
    # Recreate the important part of that state: request-local RLS GUCs are
    # absent, but the failure audit still needs to be persisted.
    await db_session.execute(text("SELECT set_config('app.current_team_id', '', true)"))
    await db_session.execute(text("SELECT set_config('app.current_user_id', '', true)"))
    await db_session.execute(text("SELECT set_config('app.current_user_role', '', true)"))
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"{API}/videos/upload",
            "headers": [],
            "client": ("127.0.0.1", 51000),
        }
    )
    request.state.request_id = "upload-failure-rls-test"

    await _record_upload_failure(
        db_session,
        request=request,
        actor_user_id=user.id,
        actor_email=user.email,
        actor_role=user.role,
        video_id=uuid.uuid4(),
        team_id=uuid.UUID(team["id"]),
        extra={"reason": "storage_presign_failed"},
    )

    failed = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_UPLOAD_FAILED,
            AuditLog.team_id == uuid.UUID(team["id"]),
            AuditLog.request_id == "upload-failure-rls-test",
        )
    )
    assert failed == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_sensitive_team_upload_requires_current_privacy_consent(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nextballup_core.settings import reload_settings

    monkeypatch.setenv("REQUIRE_PRIVACY_CONSENT_FOR_SENSITIVE_UPLOADS", "true")
    reload_settings()
    try:
        team, game = await _setup_coach_team_game(
            storage_client, coach_email="privacy-gate-coach@example.com"
        )

        missing = await storage_client.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"]),
        )
        assert missing.status_code == 403
        assert missing.json()["error"]["code"] == ErrorCode.PRIVACY_CONSENT_REQUIRED

        consent_response = await storage_client.post(
            f"{API}/teams/{team['id']}/privacy-consents",
            json=_privacy_consent_body(),
        )
        assert consent_response.status_code == 201, consent_response.text
        consent = consent_response.json()
        assert consent["is_active"] is True
        assert consent["minors_authorized"] is True

        listed = await storage_client.get(f"{API}/teams/{team['id']}/privacy-consents")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        allowed = await storage_client.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"], privacy_consent_id=consent["id"]),
        )
        assert allowed.status_code == 201, allowed.text

        video = await db_session.scalar(
            select(Video).where(Video.id == uuid.UUID(allowed.json()["id"]))
        )
        assert video is not None
        assert str(video.privacy_consent_id) == consent["id"]
        storage_usage = await db_session.scalar(
            select(UsageEvent).where(
                UsageEvent.team_id == uuid.UUID(team["id"]),
                UsageEvent.event_key == "video.storage.bytes_reserved",
            )
        )
        assert storage_usage is not None
        assert storage_usage.quantity == 250 * 1024 * 1024
    finally:
        monkeypatch.delenv("REQUIRE_PRIVACY_CONSENT_FOR_SENSITIVE_UPLOADS", raising=False)
        reload_settings()


# ---- POST /videos/{id}/complete -------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_complete_single_put_upload_creates_transcode_job(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="complete-coach@example.com"
    )
    initiate = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()
    video_id = initiate["id"]

    response = await storage_client.post(
        f"{API}/videos/{video_id}/complete",
        json={"checksum_sha256": "a" * 64},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["job_id"]
    assert body["estimated_processing_minutes"] >= 1

    video = await db_session.scalar(select(Video).where(Video.id == uuid.UUID(video_id)))
    assert video is not None
    assert video.status is VideoStatus.QUEUED
    assert video.checksum_sha256 == "a" * 64

    job = await db_session.scalar(
        select(ProcessingJob).where(ProcessingJob.video_id == uuid.UUID(video_id))
    )
    assert job is not None
    assert job.stage is ProcessingJobStage.TRANSCODE
    assert job.status is ProcessingJobStatus.PENDING

    actions = {
        row[0]
        for row in (
            await db_session.execute(
                select(AuditLog.action).where(AuditLog.team_id == uuid.UUID(team["id"]))
            )
        ).all()
    }
    assert AuditAction.VIDEO_UPLOAD_COMPLETED in actions
    assert AuditAction.VIDEO_PROCESSING_QUEUED in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_complete_is_idempotent(storage_client: AsyncClient) -> None:
    _, game = await _setup_coach_team_game(storage_client, coach_email="idem-coach@example.com")
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]

    first = await storage_client.post(
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "b" * 64}
    )
    assert first.status_code == 200
    job_id = first.json()["job_id"]

    second = await storage_client.post(
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "b" * 64}
    )
    assert second.status_code == 200
    assert second.json()["job_id"] == job_id


@pytest.mark.asyncio(loop_scope="session")
async def test_complete_multipart_requires_parts(storage_client: AsyncClient) -> None:
    _, game = await _setup_coach_team_game(storage_client, coach_email="mp-coach@example.com")
    initiate = (
        await storage_client.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"], file_size_bytes=2 * 1024 * 1024 * 1024),
        )
    ).json()
    video_id = initiate["id"]

    missing = await storage_client.post(
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "c" * 64}
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == ErrorCode.MULTIPART_PARTS_REQUIRED


@pytest.mark.asyncio(loop_scope="session")
async def test_cancel_multipart_upload_aborts_storage_and_marks_failed(
    storage_client: AsyncClient,
    fake_storage: FakeStorage,
    db_session: AsyncSession,
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="cancel-multipart@example.com"
    )
    initiate = (
        await storage_client.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"], file_size_bytes=1_500_000_000),
        )
    ).json()
    video_id = uuid.UUID(initiate["id"])

    response = await storage_client.post(f"{API}/videos/{video_id}/cancel-upload")

    assert response.status_code == 204, response.text
    assert fake_storage.aborted_multiparts[-1]["upload_id"] == initiate["upload_id"]
    video = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert video is not None
    assert video.status is VideoStatus.FAILED
    assert video.upload_id is None
    assert video.upload_expires_at is None
    usage_sum = await db_session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.team_id == uuid.UUID(team["id"]),
            UsageEvent.event_key == "video.upload.initiated",
        )
    )
    assert usage_sum == 0
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.resource_id == video_id,
            AuditLog.action == AuditAction.VIDEO_UPLOAD_ABANDONED,
        )
    )
    assert audit is not None
    assert audit.extra is not None
    assert audit.extra["reason"] == "user_cancelled"


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_pending_multipart_upload_aborts_storage_and_releases_quota(
    storage_client: AsyncClient,
    fake_storage: FakeStorage,
    db_session: AsyncSession,
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="delete-pending-multipart@example.com"
    )
    initiate = (
        await storage_client.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"], file_size_bytes=1_500_000_000),
        )
    ).json()
    video_id = uuid.UUID(initiate["id"])

    response = await storage_client.delete(f"{API}/videos/{video_id}")

    assert response.status_code == 204, response.text
    assert fake_storage.aborted_multiparts[-1]["upload_id"] == initiate["upload_id"]
    assert await db_session.scalar(select(Video).where(Video.id == video_id)) is None
    usage_sum = await db_session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.team_id == uuid.UUID(team["id"]),
            UsageEvent.event_key == "video.upload.initiated",
        )
    )
    assert usage_sum == 0
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.resource_id == video_id,
            AuditLog.action == AuditAction.VIDEO_DELETED,
        )
    )
    assert audit is not None
    assert audit.extra is not None
    assert audit.extra["quota_released"] is True
    assert audit.extra["storage_cleanup"]["aborted_multipart"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_failed_transcode_video_deletes_raw_and_releases_quota(
    storage_client: AsyncClient,
    fake_storage: FakeStorage,
    db_session: AsyncSession,
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="delete-failed-transcode@example.com"
    )
    upload = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()
    complete = (
        await storage_client.post(
            f"{API}/videos/{upload['id']}/complete",
            json={"checksum_sha256": "e" * 64},
        )
    ).json()
    video_id = uuid.UUID(complete["id"])
    job_id = uuid.UUID(complete["job_id"])
    await _force_job_status(
        db_session,
        job_id=job_id,
        new_status=ProcessingJobStatus.FAILED,
        error_message="[PROCESSING_STORAGE_FAILURE] simulated",
    )
    await _force_video_status(db_session, video_id=video_id, new_status=VideoStatus.FAILED)
    video_before = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert video_before is not None
    raw_key = video_before.storage_key_raw
    assert raw_key is not None and raw_key in fake_storage.object_sizes
    audit_count_before = await db_session.scalar(select(func.count()).select_from(AuditLog))

    response = await storage_client.delete(f"{API}/videos/{video_id}")

    assert response.status_code == 204, response.text
    assert await db_session.scalar(select(Video).where(Video.id == video_id)) is None
    assert raw_key not in fake_storage.object_sizes
    usage_sum = await db_session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.team_id == uuid.UUID(team["id"]),
            UsageEvent.event_key == "video.upload.initiated",
        )
    )
    assert usage_sum == 0
    audit_count_after = await db_session.scalar(select(func.count()).select_from(AuditLog))
    assert audit_count_before is not None and audit_count_after is not None
    assert audit_count_after > audit_count_before
    deleted_audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.resource_id == video_id,
            AuditLog.action == AuditAction.VIDEO_DELETED,
        )
    )
    assert deleted_audit is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_failed_video_records_auditable_failure_when_storage_delete_fails(
    storage_client: AsyncClient,
    fake_storage: FakeStorage,
    db_session: AsyncSession,
) -> None:
    _, game = await _setup_coach_team_game(
        storage_client, coach_email="delete-storage-fails@example.com"
    )
    upload = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()
    complete = (
        await storage_client.post(
            f"{API}/videos/{upload['id']}/complete",
            json={"checksum_sha256": "e" * 64},
        )
    ).json()
    video_id = uuid.UUID(complete["id"])
    job_id = uuid.UUID(complete["job_id"])
    await _force_job_status(
        db_session,
        job_id=job_id,
        new_status=ProcessingJobStatus.FAILED,
        error_message="[PROCESSING_STORAGE_FAILURE] simulated",
    )
    await _force_video_status(db_session, video_id=video_id, new_status=VideoStatus.FAILED)
    video_before = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert video_before is not None and video_before.storage_key_raw is not None
    fake_storage.fail_delete_for_keys.add(video_before.storage_key_raw)
    try:
        response = await storage_client.delete(f"{API}/videos/{video_id}")
    finally:
        fake_storage.fail_delete_for_keys.discard(video_before.storage_key_raw)

    assert response.status_code == 502, response.text
    persisted = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert persisted is not None
    assert persisted.raw_delete_failed_at is not None
    failed_audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.resource_id == video_id,
            AuditLog.action == AuditAction.VIDEO_DELETE_FAILED,
        )
    )
    assert failed_audit is not None
    assert failed_audit.extra is not None
    assert failed_audit.extra["failed_cleanup"] == ["raw"]


@pytest.mark.asyncio(loop_scope="session")
async def test_unauthorized_user_cannot_delete_another_teams_failed_video(
    storage_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _, game = await _setup_coach_team_game(storage_client, coach_email="delete-owner@example.com")
    upload = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()
    complete = (
        await storage_client.post(
            f"{API}/videos/{upload['id']}/complete",
            json={"checksum_sha256": "e" * 64},
        )
    ).json()
    video_id = uuid.UUID(complete["id"])
    await _force_job_status(
        db_session,
        job_id=uuid.UUID(complete["job_id"]),
        new_status=ProcessingJobStatus.FAILED,
        error_message="[PROCESSING_STORAGE_FAILURE] simulated",
    )
    await _force_video_status(db_session, video_id=video_id, new_status=VideoStatus.FAILED)

    await _register(storage_client, _coach_payload("delete-snoop@example.com"))
    response = await storage_client.delete(f"{API}/videos/{video_id}")

    assert response.status_code in {403, 404}
    assert await db_session.scalar(select(Video).where(Video.id == video_id)) is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_complete_multipart_with_parts_succeeds(
    storage_client: AsyncClient, fake_storage: FakeStorage
) -> None:
    _, game = await _setup_coach_team_game(storage_client, coach_email="mp-ok-coach@example.com")
    initiate = (
        await storage_client.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"], file_size_bytes=1_500_000_000),
        )
    ).json()
    video_id = initiate["id"]
    parts = [
        {"part_number": p["part_number"], "etag": f'"etag-{p["part_number"]}"'}
        for p in initiate["part_urls"]
    ]

    response = await storage_client.post(
        f"{API}/videos/{video_id}/complete",
        json={"checksum_sha256": "d" * 64, "parts": parts},
    )
    assert response.status_code == 200, response.text
    completed = fake_storage.completed_multiparts[-1]
    assert completed["upload_id"] == initiate["upload_id"]
    assert len(completed["parts"]) == len(parts)


@pytest.mark.asyncio(loop_scope="session")
async def test_complete_rejects_duplicate_part_numbers(
    storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(storage_client, coach_email="mp-dup-coach@example.com")
    initiate = (
        await storage_client.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"], file_size_bytes=2 * 1024 * 1024 * 1024),
        )
    ).json()
    video_id = initiate["id"]
    response = await storage_client.post(
        f"{API}/videos/{video_id}/complete",
        json={
            "checksum_sha256": "e" * 64,
            "parts": [
                {"part_number": 1, "etag": '"a"'},
                {"part_number": 1, "etag": '"b"'},
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.INVALID_MULTIPART_PARTS


@pytest.mark.asyncio(loop_scope="session")
async def test_complete_unknown_video_returns_404(storage_client: AsyncClient) -> None:
    await _register(storage_client, _coach_payload("unknown-vid@example.com"))
    response = await storage_client.post(
        f"{API}/videos/{uuid.uuid4()}/complete",
        json={"checksum_sha256": "f" * 64},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.VIDEO_NOT_FOUND


@pytest.mark.asyncio(loop_scope="session")
async def test_player_cannot_complete_upload(storage_client: AsyncClient) -> None:
    coach_email = "complete-owner@example.com"
    await _register(storage_client, _coach_payload(coach_email))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]

    await _register(storage_client, _player_payload("complete-player@example.com"))
    join = await storage_client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 12},
    )
    assert join.status_code == 200

    response = await storage_client.post(
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "a" * 64}
    )
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_complete_rejects_missing_uploaded_object(
    storage_client: AsyncClient, fake_storage: FakeStorage, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="missing-object-coach@example.com"
    )
    initiate = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()
    video_id = uuid.UUID(initiate["id"])
    video = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert video is not None
    assert video.storage_key_raw is not None
    fake_storage.object_sizes.pop(video.storage_key_raw, None)

    response = await storage_client.post(
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "b" * 64}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVALID_VIDEO_STATE

    persisted_video = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert persisted_video is not None
    assert persisted_video.status is VideoStatus.PENDING_UPLOAD

    failed = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_UPLOAD_FAILED,
            AuditLog.team_id == uuid.UUID(team["id"]),
        )
    )
    assert failed is not None and failed >= 1


# ---- GET /videos/{id} and /status -----------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_member_can_read_video_detail_and_status(
    storage_client: AsyncClient,
) -> None:
    coach = _coach_payload("read-vid-coach@example.com")
    await _register(storage_client, coach)
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]

    detail = await storage_client.get(f"{API}/videos/{video_id}")
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["status"] == "pending_upload"
    assert detail_body["playback_status"] == "uploading"
    assert detail_body["game_id"] == game["id"]
    assert set(detail_body["processing"].keys()) == {
        "transcode",
        "detection",
        "tracking",
        "court_mapping",
        "events",
        "metrics",
    }

    # Player joins so they can read but not complete.
    await _register(storage_client, _player_payload("read-vid-player@example.com"))
    join = await storage_client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 33},
    )
    assert join.status_code == 200
    status_response = await storage_client.get(f"{API}/videos/{video_id}/status")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "pending_upload"
    assert status_response.json()["playback_status"] == "uploading"


@pytest.mark.asyncio(loop_scope="session")
async def test_non_member_cannot_read_video(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _register(storage_client, _coach_payload("vid-owner@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]

    await _register(storage_client, _coach_payload("vid-snoop@example.com"))
    response = await storage_client.get(f"{API}/videos/{video_id}")
    assert response.status_code in {403, 404}


@pytest.mark.asyncio(loop_scope="session")
async def test_video_status_reports_queued_after_complete(
    storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(storage_client, coach_email="status-coach@example.com")
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]
    await storage_client.post(
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "c" * 64}
    )

    status_response = await storage_client.get(f"{API}/videos/{video_id}/status")
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "queued"
    assert body["playback_status"] == "queued"
    assert body["stages"]["transcode"]["status"] == "pending"


@pytest.mark.asyncio(loop_scope="session")
async def test_video_status_reports_running_worker_heartbeat(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, game = await _setup_coach_team_game(
        storage_client, coach_email="status-heartbeat-coach@example.com"
    )
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]
    await storage_client.post(
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "c" * 64}
    )
    heartbeat_at = datetime.now(UTC)
    await db_session.execute(
        update(Video).where(Video.id == uuid.UUID(video_id)).values(status=VideoStatus.PROCESSING)
    )
    await db_session.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.video_id == uuid.UUID(video_id),
            ProcessingJob.stage == ProcessingJobStage.TRANSCODE,
        )
        .values(
            status=ProcessingJobStatus.RUNNING,
            progress_percent=50,
            started_at=heartbeat_at,
            heartbeat_at=heartbeat_at,
        )
    )
    await db_session.commit()

    status_response = await storage_client.get(f"{API}/videos/{video_id}/status")
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "processing"
    assert body["stage"] == "transcode"
    assert body["stages"]["transcode"]["status"] == "running"
    assert body["stages"]["transcode"]["progress_percent"] == 50
    assert body["stages"]["transcode"]["started_at"] is not None
    assert body["stages"]["transcode"]["heartbeat_at"] is not None


def test_playback_status_mapping_for_lifecycle_states() -> None:
    assert (
        derive_playback_status(
            Video(status=VideoStatus.PENDING_UPLOAD),
            [],
            cv_pipeline_enabled=False,
        )
        == "uploading"
    )
    assert (
        derive_playback_status(Video(status=VideoStatus.UPLOADING), [], cv_pipeline_enabled=False)
        == "uploading"
    )
    assert (
        derive_playback_status(Video(status=VideoStatus.UPLOADED), [], cv_pipeline_enabled=False)
        == "queued"
    )
    assert (
        derive_playback_status(Video(status=VideoStatus.QUEUED), [], cv_pipeline_enabled=False)
        == "queued"
    )
    assert (
        derive_playback_status(
            Video(status=VideoStatus.TRANSCODING),
            [],
            cv_pipeline_enabled=False,
        )
        == "transcoding"
    )
    assert (
        derive_playback_status(Video(status=VideoStatus.PROCESSING), [], cv_pipeline_enabled=False)
        == "transcoding"
    )
    assert (
        derive_playback_status(Video(status=VideoStatus.FAILED), [], cv_pipeline_enabled=False)
        == "failed"
    )
    assert (
        derive_playback_status(Video(status=VideoStatus.PROCESSED), [], cv_pipeline_enabled=False)
        == "ready_for_playback"
    )
    assert (
        derive_playback_status(Video(status=VideoStatus.PROCESSED), [], cv_pipeline_enabled=True)
        == "analysis_pending"
    )
    assert (
        derive_playback_status(
            Video(status=VideoStatus.PROCESSED),
            [
                ProcessingJob(
                    stage=ProcessingJobStage.DETECTION,
                    status=ProcessingJobStatus.RUNNING,
                )
            ],
            cv_pipeline_enabled=True,
        )
        == "analysis_running"
    )
    assert (
        derive_playback_status(
            Video(status=VideoStatus.PROCESSED),
            [
                ProcessingJob(
                    stage=ProcessingJobStage.DETECTION,
                    status=ProcessingJobStatus.COMPLETED,
                )
            ],
            cv_pipeline_enabled=True,
        )
        == "ready_for_playback"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_video_events_endpoint_reflects_game_shot_clock_policy(
    storage_client: AsyncClient,
) -> None:
    await _register(storage_client, _coach_payload("events-shot-clock@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (
        await storage_client.post(f"{API}/games", json=_shot_clock_game_body(team["id"]))
    ).json()
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]

    response = await storage_client.get(f"{API}/videos/{video_id}/events")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["video_id"] == video_id
    assert body["shot_clock_enabled"] is True
    assert body["shot_clock_seconds"] == 30
    assert body["events"] == []
    assert body["total"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_clip_proposals_endpoint_returns_empty_queue_without_events(
    storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(
        storage_client, coach_email="clip-empty-coach@example.com"
    )
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]

    response = await storage_client.get(f"{API}/videos/{video_id}/clip-proposals")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["video_id"] == video_id
    assert body["proposals"] == []
    assert body["total"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_clip_proposals_endpoint_returns_ranked_event_windows(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="clip-events-coach@example.com"
    )
    upload = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    video_id = uuid.UUID(upload.json()["id"])

    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(Video).where(Video.id == video_id).values(duration_seconds=30.0)
    )
    shot = VideoEvent(
        video_id=video_id,
        team_id=uuid.UUID(team["id"]),
        event_type=VideoEventType.SHOT_MADE,
        event_time_ms=12_000,
        output_frame=360,
        shot_clock_enabled=False,
        confidence=0.72,
        review_status=ReviewStatus.NEEDS_REVIEW,
        event_metadata={"clip_pre_ms": 4_000, "internal_track_id": "track-secret"},
    )
    passing = VideoEvent(
        video_id=video_id,
        team_id=uuid.UUID(team["id"]),
        event_type=VideoEventType.PASS,
        event_time_ms=2_000,
        output_frame=60,
        shot_clock_enabled=False,
        confidence=0.99,
        review_status=ReviewStatus.NEEDS_REVIEW,
    )
    approved = VideoEvent(
        video_id=video_id,
        team_id=uuid.UUID(team["id"]),
        event_type=VideoEventType.SHOT_MADE,
        event_time_ms=16_000,
        output_frame=480,
        shot_clock_enabled=False,
        confidence=0.99,
        review_status=ReviewStatus.APPROVED,
    )
    rejected = VideoEvent(
        video_id=video_id,
        team_id=uuid.UUID(team["id"]),
        event_type=VideoEventType.SHOT_ATTEMPT,
        event_time_ms=20_000,
        output_frame=600,
        shot_clock_enabled=False,
        confidence=0.99,
        review_status=ReviewStatus.REJECTED,
    )
    db_session.add_all([shot, passing, approved, rejected])
    await db_session.commit()
    shot_id = shot.id
    await clear_worker_context(db_session)

    response = await storage_client.get(f"{API}/videos/{video_id}/clip-proposals")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert [proposal["label"] for proposal in body["proposals"]] == ["Made shot", "Pass"]
    first = body["proposals"][0]
    assert first["source_event_id"] == str(shot_id)
    assert first["event_type"] == "shot_made"
    assert first["start_time_ms"] == 8_000
    assert first["end_time_ms"] == 19_000
    assert first["review_status"] == "needs_review"
    assert first["reason"] == "Alpha made shot candidate at 00:12. Coach review required."
    assert "rank_score" not in first
    assert "confidence" not in first
    assert "internal_track_id" not in response.text

    limited = await storage_client.get(f"{API}/videos/{video_id}/clip-proposals?limit=1")
    assert limited.status_code == 200, limited.text
    assert limited.json()["total"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_clip_proposals_endpoint_rejects_non_members(
    storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(
        storage_client, coach_email="clip-owner-coach@example.com"
    )
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]

    await _register(storage_client, _coach_payload("clip-snoop-coach@example.com"))
    response = await storage_client.get(f"{API}/videos/{video_id}/clip-proposals")

    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_clip_proposals_endpoint_rejects_player_members(
    storage_client: AsyncClient,
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="clip-player-owner@example.com"
    )
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]

    await _register(storage_client, _player_payload("clip-player@example.com"))
    join = await storage_client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 44},
    )
    assert join.status_code == 200, join.text

    response = await storage_client.get(f"{API}/videos/{video_id}/clip-proposals")

    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_coach_can_review_alpha_candidate_event(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="candidate-review-coach@example.com"
    )
    upload = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    video_id = uuid.UUID(upload.json()["id"])

    await set_worker_operator_role(db_session)
    event = VideoEvent(
        video_id=video_id,
        team_id=uuid.UUID(team["id"]),
        event_type=VideoEventType.SHOT_ATTEMPT,
        event_time_ms=12_000,
        output_frame=360,
        shot_clock_enabled=False,
        confidence=None,
        review_status=ReviewStatus.NEEDS_REVIEW,
        event_metadata={
            "source": "restricted_bard_lora_alpha_video_windows",
            "not_production_analytics": True,
        },
    )
    db_session.add(event)
    await db_session.commit()
    event_id = event.id
    await clear_worker_context(db_session)

    response = await storage_client.patch(
        f"{API}/videos/{video_id}/events/{event_id}/review",
        json={
            "review_status": "approved",
            "clip_start_time_ms": 9_000,
            "clip_end_time_ms": 17_000,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(event_id)
    assert body["review_status"] == "approved"
    assert body["clip_start_time_ms"] == 9_000
    assert body["clip_end_time_ms"] == 17_000

    await set_worker_operator_role(db_session)
    persisted = await db_session.get(VideoEvent, event_id)
    assert persisted is not None
    assert persisted.review_status is ReviewStatus.APPROVED
    assert persisted.clip_start_time_ms == 9_000
    assert persisted.clip_end_time_ms == 17_000
    assert persisted.event_metadata is not None
    assert persisted.event_metadata["review_source"] == "coach_review"
    assert persisted.event_metadata["clip_pre_ms"] == 3_000
    assert persisted.event_metadata["clip_post_ms"] == 5_000
    audit_count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_EVENT_REVIEWED,
            AuditLog.resource_id == event_id,
            AuditLog.team_id == uuid.UUID(team["id"]),
        )
    )
    assert audit_count == 1
    await clear_worker_context(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_player_cannot_review_alpha_candidate_event(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="candidate-review-owner@example.com"
    )
    upload = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    video_id = uuid.UUID(upload.json()["id"])

    await set_worker_operator_role(db_session)
    event = VideoEvent(
        video_id=video_id,
        team_id=uuid.UUID(team["id"]),
        event_type=VideoEventType.REBOUND,
        event_time_ms=8_000,
        output_frame=240,
        shot_clock_enabled=False,
        review_status=ReviewStatus.NEEDS_REVIEW,
    )
    db_session.add(event)
    await db_session.commit()
    event_id = event.id
    await clear_worker_context(db_session)

    await _register(storage_client, _player_payload("candidate-review-player@example.com"))
    join = await storage_client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 24},
    )
    assert join.status_code == 200, join.text

    response = await storage_client.patch(
        f"{API}/videos/{video_id}/events/{event_id}/review",
        json={"review_status": "approved"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_coach_can_create_manual_alpha_video_event(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="manual-event-coach@example.com"
    )
    upload = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    video_id = uuid.UUID(upload.json()["id"])

    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(Video)
        .where(Video.id == video_id)
        .values(status=VideoStatus.PROCESSED, duration_seconds=120.0, fps=30.0)
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    response = await storage_client.post(
        f"{API}/videos/{video_id}/events",
        json={"event_type": "rebound", "event_time_ms": 42_000},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["event_type"] == "rebound"
    assert body["event_time_ms"] == 42_000
    assert body["clip_start_time_ms"] == 38_000
    assert body["clip_end_time_ms"] == 48_000
    assert body["output_frame"] == 1260
    assert body["review_status"] == "needs_review"

    event_id = uuid.UUID(body["id"])
    await set_worker_operator_role(db_session)
    persisted = await db_session.get(VideoEvent, event_id)
    assert persisted is not None
    assert persisted.team_id == uuid.UUID(team["id"])
    assert persisted.event_metadata == {
        "source": "coach_manual_alpha_tag",
        "not_production_analytics": True,
        "review_copy": "Coach-created alpha tag. Review before export.",
        "clip_pre_ms": 4_000,
        "clip_post_ms": 6_000,
    }
    audit_count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_EVENT_MANUAL_CREATED,
            AuditLog.resource_id == event_id,
            AuditLog.team_id == uuid.UUID(team["id"]),
        )
    )
    assert audit_count == 1
    await clear_worker_context(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_coach_can_export_approved_event_windows_without_metadata_leak(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="events-export-coach@example.com"
    )
    upload = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    video_id = uuid.UUID(upload.json()["id"])
    team_id = uuid.UUID(team["id"])

    await set_worker_operator_role(db_session)
    approved = VideoEvent(
        video_id=video_id,
        team_id=team_id,
        event_type=VideoEventType.SHOT_ATTEMPT,
        event_time_ms=12_000,
        clip_start_time_ms=8_000,
        clip_end_time_ms=18_000,
        output_frame=360,
        shot_clock_enabled=False,
        review_status=ReviewStatus.APPROVED,
        event_metadata={
            "source": "restricted_bard_lora_alpha_video_windows",
            "internal_track_id": "track-secret",
            "model_artifact_uri": "s3://private/model",
        },
    )
    rejected = VideoEvent(
        video_id=video_id,
        team_id=team_id,
        event_type=VideoEventType.REBOUND,
        event_time_ms=20_000,
        clip_start_time_ms=16_000,
        clip_end_time_ms=26_000,
        output_frame=600,
        shot_clock_enabled=False,
        review_status=ReviewStatus.REJECTED,
    )
    db_session.add_all([approved, rejected])
    await db_session.commit()
    approved_id = approved.id
    await clear_worker_context(db_session)

    response = await storage_client.get(f"{API}/videos/{video_id}/events/export?format=csv")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "internal_track_id" not in response.text
    assert "model_artifact_uri" not in response.text
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows == [
        {
            "video_id": str(video_id),
            "event_id": str(approved_id),
            "event_type": "shot_attempt",
            "review_status": "approved",
            "source": "alpha_model",
            "clip_start_time_ms": "8000",
            "clip_end_time_ms": "18000",
            "event_time_ms": "12000",
            "created_at": rows[0]["created_at"],
        }
    ]

    await set_worker_operator_role(db_session)
    audit_count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_EVENT_EXPORT_CREATED,
            AuditLog.resource_id == video_id,
            AuditLog.team_id == team_id,
        )
    )
    assert audit_count == 1
    await clear_worker_context(db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_video_events_endpoint_paginates_beyond_100_candidates(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="events-pagination-coach@example.com"
    )
    upload = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    video_id = uuid.UUID(upload.json()["id"])

    await set_worker_operator_role(db_session)
    rows = [
        VideoEvent(
            video_id=video_id,
            team_id=uuid.UUID(team["id"]),
            event_type=VideoEventType.SHOT_ATTEMPT,
            event_time_ms=1_000 * (index + 1),
            output_frame=30 * (index + 1),
            shot_clock_enabled=False,
            review_status=ReviewStatus.NEEDS_REVIEW,
            event_metadata={"source": "restricted_bard_lora_alpha_video_windows"},
        )
        for index in range(120)
    ]
    db_session.add_all(rows)
    await db_session.commit()
    await clear_worker_context(db_session)

    first = await storage_client.get(f"{API}/videos/{video_id}/events?limit=100")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["total"] == 120
    assert body["summary"]["total"] == 120
    assert body["summary"]["needs_review"] == 120
    assert body["summary"]["alpha_model_source"] == 120
    assert body["summary"]["manual_source"] == 0
    assert len(body["events"]) == 100
    assert body["next_cursor"] is not None
    assert body["events"][0]["source"] == "alpha_model"

    second = await storage_client.get(
        f"{API}/videos/{video_id}/events?limit=100&cursor={body['next_cursor']}"
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert len(second_body["events"]) == 20
    assert second_body["next_cursor"] is None
    first_ids = {entry["id"] for entry in body["events"]}
    second_ids = {entry["id"] for entry in second_body["events"]}
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.asyncio(loop_scope="session")
async def test_video_events_endpoint_filters_by_status_type_and_source(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="events-filters-coach@example.com"
    )
    upload = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    video_id = uuid.UUID(upload.json()["id"])

    await set_worker_operator_role(db_session)
    db_session.add_all(
        [
            VideoEvent(
                video_id=video_id,
                team_id=uuid.UUID(team["id"]),
                event_type=VideoEventType.SHOT_ATTEMPT,
                event_time_ms=1_000,
                output_frame=30,
                shot_clock_enabled=False,
                review_status=ReviewStatus.NEEDS_REVIEW,
                event_metadata={"source": "restricted_bard_lora_alpha_video_windows"},
            ),
            VideoEvent(
                video_id=video_id,
                team_id=uuid.UUID(team["id"]),
                event_type=VideoEventType.REBOUND,
                event_time_ms=2_000,
                output_frame=60,
                shot_clock_enabled=False,
                review_status=ReviewStatus.APPROVED,
                event_metadata={"source": "restricted_bard_lora_alpha_video_windows"},
            ),
            VideoEvent(
                video_id=video_id,
                team_id=uuid.UUID(team["id"]),
                event_type=VideoEventType.PASS,
                event_time_ms=3_000,
                output_frame=90,
                shot_clock_enabled=False,
                review_status=ReviewStatus.REJECTED,
                event_metadata={"source": "restricted_bard_lora_alpha_video_windows"},
            ),
            VideoEvent(
                video_id=video_id,
                team_id=uuid.UUID(team["id"]),
                event_type=VideoEventType.SHOT_MADE,
                event_time_ms=4_000,
                output_frame=120,
                shot_clock_enabled=False,
                review_status=ReviewStatus.NEEDS_REVIEW,
                event_metadata={"source": "coach_manual_alpha_tag"},
            ),
        ]
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    needs = await storage_client.get(f"{API}/videos/{video_id}/events?review_status=needs_review")
    assert needs.status_code == 200, needs.text
    needs_body = needs.json()
    assert needs_body["total"] == 2
    assert {entry["event_type"] for entry in needs_body["events"]} == {
        "shot_attempt",
        "shot_made",
    }

    approved = await storage_client.get(f"{API}/videos/{video_id}/events?review_status=approved")
    approved_body = approved.json()
    assert approved_body["total"] == 1
    assert approved_body["events"][0]["event_type"] == "rebound"
    assert approved_body["events"][0]["review_status"] == "approved"

    rejected = await storage_client.get(f"{API}/videos/{video_id}/events?review_status=rejected")
    rejected_body = rejected.json()
    assert rejected_body["total"] == 1
    assert rejected_body["events"][0]["event_type"] == "pass"

    rebounds = await storage_client.get(f"{API}/videos/{video_id}/events?event_type=rebound")
    assert rebounds.json()["total"] == 1

    manual = await storage_client.get(f"{API}/videos/{video_id}/events?source=manual")
    manual_body = manual.json()
    assert manual_body["total"] == 1
    assert manual_body["events"][0]["source"] == "manual"

    alpha = await storage_client.get(f"{API}/videos/{video_id}/events?source=alpha_model")
    alpha_body = alpha.json()
    assert alpha_body["total"] == 3
    assert all(entry["source"] == "alpha_model" for entry in alpha_body["events"])

    # Summary remains the unfiltered totals regardless of applied filters.
    assert needs_body["summary"]["total"] == 4
    assert needs_body["summary"]["needs_review"] == 2
    assert needs_body["summary"]["approved"] == 1
    assert needs_body["summary"]["rejected"] == 1
    assert needs_body["summary"]["alpha_model_source"] == 3
    assert needs_body["summary"]["manual_source"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_video_events_endpoint_does_not_leak_internal_metadata(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="events-metadata-coach@example.com"
    )
    upload = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    video_id = uuid.UUID(upload.json()["id"])

    await set_worker_operator_role(db_session)
    db_session.add(
        VideoEvent(
            video_id=video_id,
            team_id=uuid.UUID(team["id"]),
            event_type=VideoEventType.SHOT_ATTEMPT,
            event_time_ms=5_000,
            output_frame=150,
            shot_clock_enabled=False,
            review_status=ReviewStatus.NEEDS_REVIEW,
            event_metadata={
                "source": "restricted_bard_lora_alpha_video_windows",
                "internal_track_id": "track-confidential",
                "model_artifact_uri": "s3://restricted-bucket/secret-model.bin",
                "raw_prompt": "do not leak this",
            },
        )
    )
    await db_session.commit()
    await clear_worker_context(db_session)

    response = await storage_client.get(f"{API}/videos/{video_id}/events")
    assert response.status_code == 200, response.text
    body = response.text
    # Internal model and dataset lineage stays server-side; the API only
    # exposes the externally safe `source` enum (alpha_model / manual).
    assert "restricted_bard_lora_alpha_video_windows" not in body
    assert "internal_track_id" not in body
    assert "track-confidential" not in body
    assert "model_artifact_uri" not in body
    assert "restricted-bucket" not in body
    assert "raw_prompt" not in body
    assert "do not leak this" not in body
    parsed = response.json()
    assert parsed["events"][0]["source"] == "alpha_model"


@pytest.mark.asyncio(loop_scope="session")
async def test_video_events_endpoint_rejects_player_members(
    storage_client: AsyncClient,
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="events-player-owner@example.com"
    )
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]

    await _register(storage_client, _player_payload("events-player@example.com"))
    join = await storage_client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 33},
    )
    assert join.status_code == 200, join.text

    response = await storage_client.get(f"{API}/videos/{video_id}/events")
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_video_events_endpoint_rejects_non_members(
    storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(
        storage_client, coach_email="events-tenant-owner@example.com"
    )
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]

    await _register(storage_client, _coach_payload("events-cross-team-snoop@example.com"))
    response = await storage_client.get(f"{API}/videos/{video_id}/events")
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_video_events_endpoint_rejects_invalid_cursor(
    storage_client: AsyncClient,
) -> None:
    _, game = await _setup_coach_team_game(
        storage_client, coach_email="events-bad-cursor-coach@example.com"
    )
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]

    response = await storage_client.get(f"{API}/videos/{video_id}/events?cursor=not-base64!!!")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_can_read_cross_team_game_and_video(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="admin-read-owner@example.com"
    )
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]
    headers = await _seed_admin_headers(db_session, email="admin-reader@example.com")

    storage_client.cookies.clear()
    game_response = await storage_client.get(f"{API}/games/{game['id']}", headers=headers)
    assert game_response.status_code == 200, game_response.text
    assert game_response.json()["team_id"] == team["id"]

    video_response = await storage_client.get(f"{API}/videos/{video_id}", headers=headers)
    assert video_response.status_code == 200, video_response.text
    assert video_response.json()["game_id"] == game["id"]


# ---- POST /videos/{id}/demo-preview --------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_video_detail_includes_demo_preview_when_available(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    demo_preview_env: dict[str, Path],
) -> None:
    _, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="demo-detail@example.com",
    )
    preview_path = demo_preview_env["preview_root"] / str(video_id) / "demo-preview.annotated.mp4"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(b"preview")

    response = await storage_client.get(f"{API}/videos/{video_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["demo_preview_enabled"] is True
    assert body["demo_preview_status"] == "completed"
    assert body["demo_preview_url"] == f"/api/v1/videos/{video_id}/demo-preview/artifact"
    assert body["demo_preview_generated_at"] is not None
    assert body["demo_preview_error_message"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_video_detail_includes_shared_alpha_demo_preview_artifact(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    alpha_preview_env: dict[str, Path],
) -> None:
    assert alpha_preview_env["preview_root"].name == "alpha_demo_previews"
    assert get_settings().local_demo_preview_enabled() is True
    _, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="demo-detail-shared@example.com",
    )
    preview_key = f"artifacts/shared-team/{video_id}/demo-preview.annotated.mp4"
    fake_storage.object_sizes[preview_key] = 7
    generated_at = datetime.now(tz=UTC)
    video = await db_session.get(Video, video_id)
    assert video is not None
    video.demo_preview_status = "completed"
    video.demo_preview_storage_key = preview_key
    video.demo_preview_generated_at = generated_at
    await db_session.commit()

    response = await storage_client.get(f"{API}/videos/{video_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["demo_preview_enabled"] is True
    assert body["demo_preview_status"] == "completed"
    assert body["demo_preview_url"] == f"/api/v1/videos/{video_id}/demo-preview/artifact"
    assert body["demo_preview_generated_at"] is not None

    artifact = await storage_client.get(f"{API}/videos/{video_id}/demo-preview/artifact")
    assert artifact.status_code == 307, artifact.text
    assert artifact.headers["location"].startswith(f"https://fake-storage.test/{preview_key}")
    assert "exp=7200" in artifact.headers["location"]


@pytest.mark.asyncio(loop_scope="session")
async def test_coach_can_queue_demo_preview(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    demo_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="demo-generate@example.com",
    )

    monkeypatch.setattr(
        "nextballup_api.demo_preview._enqueue_demo_preview_task",
        lambda *, video_id, settings: f"task-{video_id}",
    )

    response = await storage_client.post(f"{API}/videos/{video_id}/demo-preview")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["preview_url"] is None
    assert body["generated_at"] is None

    detail = await storage_client.get(f"{API}/videos/{video_id}")
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["demo_preview_status"] == "queued"
    assert detail_body["demo_preview_url"] is None
    assert detail_body["demo_preview_generated_at"] is None

    action_count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_DEMO_PREVIEW_REQUESTED,
            AuditLog.team_id == uuid.UUID(team["id"]),
        )
    )
    assert action_count is not None and action_count >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_alpha_preview_enqueue_ignores_stale_api_local_state(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    alpha_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="alpha-preview-stale-api-state@example.com",
    )
    video = await db_session.get(Video, video_id)
    assert video is not None
    video.demo_preview_status = "failed"
    video.demo_preview_task_id = "previous-failed-task"
    video.demo_preview_error_message = "Local demo preview inference failed"
    await db_session.commit()

    mark_demo_preview_queued(
        settings=get_settings(),
        video_id=video_id,
        task_id="stale-api-local-task",
        generated_at=None,
    )
    assert alpha_preview_env["preview_root"].is_dir()

    enqueue_calls: list[uuid.UUID] = []

    def _fake_enqueue(*, video_id: uuid.UUID, settings: Any) -> str:
        enqueue_calls.append(video_id)
        return f"task-{video_id}"

    monkeypatch.setattr("nextballup_api.demo_preview._enqueue_demo_preview_task", _fake_enqueue)

    response = await storage_client.post(f"{API}/videos/{video_id}/demo-preview")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    assert enqueue_calls == [video_id]

    detail = await storage_client.get(f"{API}/videos/{video_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["demo_preview_status"] == "queued"
    assert body["demo_preview_error_message"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_coach_can_cancel_queued_demo_preview(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    demo_preview_env: dict[str, Path],
) -> None:
    team, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="demo-cancel@example.com",
    )
    video = await db_session.get(Video, video_id)
    assert video is not None
    video.demo_preview_status = "queued"
    video.demo_preview_task_id = "demo-preview-task"
    video.demo_preview_requested_at = datetime.now(tz=UTC)
    await db_session.commit()

    response = await storage_client.delete(f"{API}/videos/{video_id}/demo-preview")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"

    detail = await storage_client.get(f"{API}/videos/{video_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["demo_preview_status"] == "failed"
    assert "cancelled" in body["demo_preview_error_message"].lower()

    action_count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_DEMO_PREVIEW_CANCELLED,
            AuditLog.team_id == uuid.UUID(team["id"]),
        )
    )
    assert action_count is not None and action_count >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_demo_preview_generation_is_idempotent_while_queued(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    demo_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="demo-concurrent@example.com",
    )
    enqueue_calls = 0

    def _fake_enqueue(*, video_id: uuid.UUID, settings: Any) -> str:
        nonlocal enqueue_calls
        enqueue_calls += 1
        time.sleep(0.05)
        return f"task-{video_id}"

    monkeypatch.setattr("nextballup_api.demo_preview._enqueue_demo_preview_task", _fake_enqueue)

    video_row = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert video_row is not None
    video = cast(
        "Video",
        SimpleNamespace(
            id=video_row.id,
            status=video_row.status,
            storage_key_mezzanine=video_row.storage_key_mezzanine,
        ),
    )
    settings = get_settings()

    first_result, second_result = await asyncio.gather(
        asyncio.to_thread(
            queue_demo_preview_request,
            video=video,
            settings=settings,
            storage=fake_storage,
        ),
        asyncio.to_thread(
            queue_demo_preview_request,
            video=video,
            settings=settings,
            storage=fake_storage,
        ),
    )

    assert first_result.response.status == "queued"
    assert second_result.response.status == "queued"
    assert first_result.enqueued is True or second_result.enqueued is True
    assert enqueue_calls == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_demo_preview_enqueue_failure_is_audited(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    demo_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="demo-enqueue-fail@example.com",
    )

    def _fail_enqueue(*, video_id: uuid.UUID, settings: Any) -> str:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr("nextballup_api.demo_preview._enqueue_demo_preview_task", _fail_enqueue)

    response = await storage_client.post(f"{API}/videos/{video_id}/demo-preview")
    assert response.status_code == 503, response.text

    action_count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_DEMO_PREVIEW_REJECTED,
            AuditLog.team_id == uuid.UUID(team["id"]),
        )
    )
    assert action_count is not None and action_count >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_demo_preview_enqueue_failure_preserves_completed_preview(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    demo_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="demo-enqueue-keep-completed@example.com",
    )
    preview_path = demo_preview_env["preview_root"] / str(video_id) / "demo-preview.annotated.mp4"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(b"preview")

    def _fail_enqueue(*, video_id: uuid.UUID, settings: Any) -> str:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr("nextballup_api.demo_preview._enqueue_demo_preview_task", _fail_enqueue)

    response = await storage_client.post(f"{API}/videos/{video_id}/demo-preview")
    assert response.status_code == 503, response.text

    detail = await storage_client.get(f"{API}/videos/{video_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["demo_preview_status"] == "completed"
    assert body["demo_preview_url"] == f"/api/v1/videos/{video_id}/demo-preview/artifact"
    assert body["demo_preview_error_message"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_failed_demo_preview_state_reconciles_to_completed_when_artifact_exists(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    demo_preview_env: dict[str, Path],
) -> None:
    _, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="demo-heal-failed@example.com",
    )
    preview_path = demo_preview_env["preview_root"] / str(video_id) / "demo-preview.annotated.mp4"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(b"preview")
    state_path = preview_path.parent / "demo-preview.state.json"
    state_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "requested_at": datetime.now(tz=UTC).isoformat(),
                "started_at": None,
                "generated_at": None,
                "task_id": "task-demo",
                "error_message": "stale failure",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    response = await storage_client.get(f"{API}/videos/{video_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["demo_preview_status"] == "completed"
    assert body["demo_preview_url"] == f"/api/v1/videos/{video_id}/demo-preview/artifact"
    assert body["demo_preview_error_message"] is None

    persisted_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted_state["status"] == "completed"
    assert persisted_state["error_message"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_demo_preview_rate_limit_rejection_is_audited(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    demo_preview_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="demo-rate-limit@example.com",
    )

    async def _reject_rate_limit(*args: Any, **kwargs: Any) -> None:
        raise TooManyRequestsError("Too many demo preview requests")

    monkeypatch.setattr("nextballup_api.routers.videos.enforce_rate_limit", _reject_rate_limit)

    response = await storage_client.post(f"{API}/videos/{video_id}/demo-preview")
    assert response.status_code == 429, response.text

    action_count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_DEMO_PREVIEW_REJECTED,
            AuditLog.team_id == uuid.UUID(team["id"]),
        )
    )
    assert action_count is not None and action_count >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_player_cannot_generate_demo_preview(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    demo_preview_env: dict[str, Path],
) -> None:
    team, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="demo-player-owner@example.com",
    )
    await _register(storage_client, _player_payload("demo-player@example.com"))
    join = await storage_client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 24},
    )
    assert join.status_code == 200

    response = await storage_client.post(f"{API}/videos/{video_id}/demo-preview")
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_player_cannot_cancel_demo_preview(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    demo_preview_env: dict[str, Path],
) -> None:
    team, video_id = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="demo-cancel-player-owner@example.com",
    )
    video = await db_session.get(Video, video_id)
    assert video is not None
    video.demo_preview_status = "queued"
    video.demo_preview_task_id = "demo-preview-task-player"
    await db_session.commit()

    await _register(storage_client, _player_payload("demo-cancel-player@example.com"))
    join = await storage_client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 25},
    )
    assert join.status_code == 200

    response = await storage_client.delete(f"{API}/videos/{video_id}/demo-preview")
    assert response.status_code == 403


# ---- Audit + counts -------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_audit_log_count_grows_with_upload_lifecycle(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    coach = _coach_payload("audit-vid@example.com")
    await _register(storage_client, coach)
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()
    video_id = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()["id"]
    await storage_client.post(
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "d" * 64}
    )

    counts: dict[str, int] = {}
    for action in (
        AuditAction.GAME_CREATED,
        AuditAction.VIDEO_UPLOAD_INITIATED,
        AuditAction.VIDEO_UPLOAD_COMPLETED,
        AuditAction.VIDEO_PROCESSING_QUEUED,
    ):
        counts[action] = (
            await db_session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == action,
                    AuditLog.team_id == uuid.UUID(team["id"]),
                )
            )
            or 0
        )
    assert all(v >= 1 for v in counts.values()), counts


# ---- POST /videos/{id}/playback/verify ------------------------------------


async def _mint_playback_token(
    db_session: AsyncSession,
    *,
    user_id: uuid.UUID,
    video_id: uuid.UUID,
    team_id: uuid.UUID,
    session_version: int | None = None,
    role: UserRole | None = None,
) -> str:
    from nextballup_api.security.jwt import create_playback_token

    user_row = await db_session.get(User, user_id)
    assert user_row is not None
    token, _ = create_playback_token(
        subject=user_id,
        role=role or user_row.role,
        session_version=(
            session_version if session_version is not None else user_row.session_version
        ),
        video_id=video_id,
        team_id=team_id,
        settings=get_settings(),
    )
    return token


@pytest.mark.asyncio(loop_scope="session")
async def test_playback_verify_accepts_live_token(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="pb-verify-ok@example.com"
    )
    video_id = uuid.UUID(
        (await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))).json()[
            "id"
        ]
    )
    coach = await db_session.scalar(select(User).where(User.email == "pb-verify-ok@example.com"))
    assert coach is not None
    token = await _mint_playback_token(
        db_session,
        user_id=coach.id,
        video_id=video_id,
        team_id=uuid.UUID(team["id"]),
    )
    response = await storage_client.post(
        f"{API}/videos/{video_id}/playback/verify", json={"token": token}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["video_id"] == str(video_id)
    assert "expires_at" in body


@pytest.mark.asyncio(loop_scope="session")
async def test_playback_verify_rejects_stale_session_version(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Token minted at session_version N is rejected once the user's live
    session_version moves past N — the exact post-logout revocation path."""
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="pb-verify-sv@example.com"
    )
    video_id = uuid.UUID(
        (await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))).json()[
            "id"
        ]
    )
    coach = await db_session.scalar(select(User).where(User.email == "pb-verify-sv@example.com"))
    assert coach is not None
    stale_token = await _mint_playback_token(
        db_session,
        user_id=coach.id,
        video_id=video_id,
        team_id=uuid.UUID(team["id"]),
        session_version=coach.session_version - 1,
    )
    response = await storage_client.post(
        f"{API}/videos/{video_id}/playback/verify", json={"token": stale_token}
    )
    assert response.status_code == 401


@pytest.mark.asyncio(loop_scope="session")
async def test_playback_verify_rejects_mismatched_video(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    """A token minted for video X is rejected when replayed at verify(Y)."""
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="pb-verify-vid@example.com"
    )
    video_id = uuid.UUID(
        (await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))).json()[
            "id"
        ]
    )
    coach = await db_session.scalar(select(User).where(User.email == "pb-verify-vid@example.com"))
    assert coach is not None
    # Token minted for a different video id.
    wrong_token = await _mint_playback_token(
        db_session,
        user_id=coach.id,
        video_id=uuid.uuid4(),
        team_id=uuid.UUID(team["id"]),
    )
    response = await storage_client.post(
        f"{API}/videos/{video_id}/playback/verify", json={"token": wrong_token}
    )
    assert response.status_code == 401


@pytest.mark.asyncio(loop_scope="session")
async def test_playback_verify_rejects_other_users_token(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    """A token minted for user A cannot be verified while user B is the
    authenticated caller — defeats token-sharing across accounts."""
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="pb-verify-owner@example.com"
    )
    video_id = uuid.UUID(
        (await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))).json()[
            "id"
        ]
    )
    owner = await db_session.scalar(select(User).where(User.email == "pb-verify-owner@example.com"))
    assert owner is not None
    owner_token = await _mint_playback_token(
        db_session,
        user_id=owner.id,
        video_id=video_id,
        team_id=uuid.UUID(team["id"]),
    )
    # Switch to a different authenticated session.
    await _register(storage_client, _coach_payload("pb-verify-snoop@example.com"))
    response = await storage_client.post(
        f"{API}/videos/{video_id}/playback/verify", json={"token": owner_token}
    )
    # Snoop is not on the team → team-member check returns 403; if it ever
    # resolves before that, the user-id binding returns 401. Either is a
    # correct rejection.
    assert response.status_code in {401, 403}


# ---- POST /videos/{id}/processing/requeue --------------------------------


async def _force_job_status(
    db_session: AsyncSession,
    *,
    job_id: uuid.UUID,
    new_status: ProcessingJobStatus,
    error_message: str | None = None,
) -> None:
    """Bypass the worker lifecycle and plant a terminal status directly.

    Done via the worker operator role because the test session's normal
    user-scoped RLS policies would block an UPDATE on a processing_job
    that isn't part of the current request's tenancy context.
    """
    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .values(status=new_status, error_message=error_message)
    )
    await db_session.commit()
    await clear_worker_context(db_session)


async def _force_video_status(
    db_session: AsyncSession,
    *,
    video_id: uuid.UUID,
    new_status: VideoStatus,
) -> None:
    await set_worker_operator_role(db_session)
    await db_session.execute(update(Video).where(Video.id == video_id).values(status=new_status))
    await db_session.commit()
    await clear_worker_context(db_session)


async def seeded_failed_video_for_requeue(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    *,
    coach_email: str,
) -> tuple[str, uuid.UUID, uuid.UUID]:
    team, game = await _setup_coach_team_game(storage_client, coach_email=coach_email)
    upload = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()
    complete = (
        await storage_client.post(
            f"{API}/videos/{upload['id']}/complete",
            json={"checksum_sha256": "e" * 64},
        )
    ).json()
    job_id = uuid.UUID(complete["job_id"])
    video_id = uuid.UUID(complete["id"])
    await _force_job_status(
        db_session,
        job_id=job_id,
        new_status=ProcessingJobStatus.FAILED,
        error_message="[PROCESSING_STORAGE_FAILURE] simulated",
    )
    await _force_video_status(
        db_session,
        video_id=video_id,
        new_status=VideoStatus.FAILED,
    )
    return team["id"], video_id, job_id


async def seeded_running_video_for_cancel(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    *,
    coach_email: str,
) -> tuple[str, uuid.UUID, uuid.UUID]:
    team, game = await _setup_coach_team_game(storage_client, coach_email=coach_email)
    upload = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()
    complete = (
        await storage_client.post(
            f"{API}/videos/{upload['id']}/complete",
            json={"checksum_sha256": "f" * 64},
        )
    ).json()
    job_id = uuid.UUID(complete["job_id"])
    video_id = uuid.UUID(complete["id"])
    heartbeat_at = datetime.now(UTC)
    await set_worker_operator_role(db_session)
    await db_session.execute(
        update(Video).where(Video.id == video_id).values(status=VideoStatus.PROCESSING)
    )
    await db_session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .values(
            status=ProcessingJobStatus.RUNNING,
            progress_percent=50,
            celery_task_id="stuck-transcode-task",
            started_at=heartbeat_at,
            heartbeat_at=heartbeat_at,
        )
    )
    await db_session.commit()
    await clear_worker_context(db_session)
    return team["id"], video_id, job_id


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_failed_job(
    storage_client: AsyncClient, db_session: AsyncSession
) -> tuple[str, uuid.UUID, uuid.UUID]:
    """Returns (team_id, video_id, transcode_job_id) for a video whose
    transcode has been force-failed."""
    return await seeded_failed_video_for_requeue(
        storage_client,
        db_session,
        coach_email="requeue-owner@example.com",
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_cancel_running_transcode_marks_failed_for_team_coach(
    storage_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    team_id, video_id, job_id = await seeded_running_video_for_cancel(
        storage_client,
        db_session,
        coach_email="cancel-processing-owner@example.com",
    )

    response = await storage_client.post(
        f"{API}/videos/{video_id}/processing/cancel",
        json={"stage": "transcode"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == str(job_id)
    assert body["stage"] == "transcode"
    assert body["status"] == "failed"

    await set_worker_operator_role(db_session)
    job = await db_session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .execution_options(populate_existing=True)
    )
    video = await db_session.scalar(
        select(Video).where(Video.id == video_id).execution_options(populate_existing=True)
    )
    audit = await db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.team_id == uuid.UUID(team_id),
            AuditLog.action == AuditAction.VIDEO_PROCESSING_CANCELLED,
        )
        .order_by(AuditLog.created_at.desc())
    )
    await clear_worker_context(db_session)
    assert job is not None
    assert job.status is ProcessingJobStatus.FAILED
    assert job.completed_at is not None
    assert job.error_message is not None
    assert ErrorCode.PROCESSING_CANCELLED in job.error_message
    assert (job.result_metadata or {})["cancel_reason"] == "user_cancelled_processing"
    assert video is not None
    assert video.status is VideoStatus.FAILED
    assert audit is not None
    assert audit.extra is not None
    assert audit.extra["stage"] == "transcode"
    assert audit.extra["had_celery_task_id"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_unauthorized_user_cannot_cancel_another_teams_running_transcode(
    storage_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _team_id, video_id, _job_id = await seeded_running_video_for_cancel(
        storage_client,
        db_session,
        coach_email="cancel-processing-isolated@example.com",
    )
    await _register(storage_client, _coach_payload("cancel-processing-snoop@example.com"))

    response = await storage_client.post(
        f"{API}/videos/{video_id}/processing/cancel",
        json={"stage": "transcode"},
    )

    assert response.status_code in {403, 404}


@pytest.mark.asyncio(loop_scope="session")
async def test_requeue_resets_failed_job_to_pending_for_admin(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    seeded_failed_job: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    _team_id, video_id, job_id = seeded_failed_job
    headers = await _seed_admin_headers(db_session, email="requeue-admin@example.com")

    storage_client.cookies.clear()
    response = await storage_client.post(
        f"{API}/videos/{video_id}/processing/requeue",
        json={"stage": "transcode"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == str(job_id)
    assert body["stage"] == "transcode"
    assert body["status"] == "pending"

    # The on-disk row must now be PENDING with its failure residue cleared.
    await set_worker_operator_role(db_session)
    job = await db_session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .execution_options(populate_existing=True)
    )
    await clear_worker_context(db_session)
    assert job is not None
    assert job.status is ProcessingJobStatus.PENDING
    assert job.error_message is None
    assert job.celery_task_id is None
    assert job.progress_percent == 0
    assert job.started_at is None
    assert job.completed_at is None
    video = await db_session.scalar(
        select(Video).where(Video.id == video_id).execution_options(populate_existing=True)
    )
    assert video is not None
    assert video.status is VideoStatus.QUEUED


@pytest.mark.asyncio(loop_scope="session")
async def test_requeue_writes_audit_entry(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    seeded_failed_job: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    team_id, video_id, _ = seeded_failed_job
    headers = await _seed_admin_headers(db_session, email="requeue-audit-admin@example.com")

    storage_client.cookies.clear()
    resp = await storage_client.post(
        f"{API}/videos/{video_id}/processing/requeue",
        json={"stage": "transcode"},
        headers=headers,
    )
    assert resp.status_code == 200

    count = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_PROCESSING_REQUEUED,
            AuditLog.team_id == uuid.UUID(team_id),
        )
    )
    assert (count or 0) >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_requeue_allows_team_coach_for_failed_transcode(
    storage_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _team_id, video_id, job_id = await seeded_failed_video_for_requeue(
        storage_client,
        db_session,
        coach_email="requeue-coach-owner@example.com",
    )
    response = await storage_client.post(
        f"{API}/videos/{video_id}/processing/requeue",
        json={"stage": "transcode"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == str(job_id)
    video = await db_session.scalar(
        select(Video).where(Video.id == video_id).execution_options(populate_existing=True)
    )
    assert video is not None
    assert video.status is VideoStatus.QUEUED


@pytest.mark.asyncio(loop_scope="session")
async def test_unauthorized_user_cannot_requeue_another_teams_video(
    storage_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _team_id, video_id, _job_id = await seeded_failed_video_for_requeue(
        storage_client,
        db_session,
        coach_email="requeue-owner-isolated@example.com",
    )
    await _register(storage_client, _coach_payload("requeue-snoop@example.com"))

    response = await storage_client.post(
        f"{API}/videos/{video_id}/processing/requeue",
        json={"stage": "transcode"},
    )

    assert response.status_code in {403, 404}


@pytest.mark.asyncio(loop_scope="session")
async def test_requeue_rejects_failed_transcode_when_raw_object_missing(
    storage_client: AsyncClient,
    fake_storage: FakeStorage,
    db_session: AsyncSession,
) -> None:
    _team_id, video_id, _job_id = await seeded_failed_video_for_requeue(
        storage_client,
        db_session,
        coach_email="requeue-missing-raw@example.com",
    )
    video = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert video is not None and video.storage_key_raw is not None
    fake_storage.object_sizes.pop(video.storage_key_raw, None)

    response = await storage_client.post(
        f"{API}/videos/{video_id}/processing/requeue",
        json={"stage": "transcode"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVALID_VIDEO_STATE
    assert response.json()["error"]["details"]["reason"] == "raw_object_missing"


@pytest.mark.asyncio(loop_scope="session")
async def test_requeue_rejects_unknown_stage(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    seeded_failed_job: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    _team_id, video_id, _ = seeded_failed_job
    headers = await _seed_admin_headers(db_session, email="requeue-badstage@example.com")

    storage_client.cookies.clear()
    response = await storage_client.post(
        f"{API}/videos/{video_id}/processing/requeue",
        json={"stage": "does_not_exist"},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.PROCESSING_STAGE_UNKNOWN


@pytest.mark.asyncio(loop_scope="session")
async def test_requeue_rejects_active_job(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    """A job that's still PENDING or RUNNING must not be requeuable — otherwise
    two workers could race on the same row."""
    team, game = await _setup_coach_team_game(
        storage_client, coach_email="requeue-active@example.com"
    )
    upload = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()
    complete = (
        await storage_client.post(
            f"{API}/videos/{upload['id']}/complete",
            json={"checksum_sha256": "f" * 64},
        )
    ).json()
    job_id = uuid.UUID(complete["job_id"])
    video_id = uuid.UUID(complete["id"])
    await _force_video_status(db_session, video_id=video_id, new_status=VideoStatus.FAILED)

    # PENDING is the default post-complete status, no mutation needed.
    headers = await _seed_admin_headers(db_session, email="requeue-active-admin@example.com")
    storage_client.cookies.clear()
    response = await storage_client.post(
        f"{API}/videos/{video_id}/processing/requeue",
        json={"stage": "transcode"},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.PROCESSING_JOB_NOT_REQUEUABLE

    # Now push to RUNNING and confirm it still rejects.
    await _force_job_status(db_session, job_id=job_id, new_status=ProcessingJobStatus.RUNNING)
    response = await storage_client.post(
        f"{API}/videos/{video_id}/processing/requeue",
        json={"stage": "transcode"},
        headers=headers,
    )
    assert response.status_code == 409
    _ = team  # silence unused


@pytest.mark.asyncio(loop_scope="session")
async def test_requeue_missing_stage_returns_404(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    seeded_failed_job: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    """Only transcode retries are supported by the alpha recovery path."""
    _team_id, video_id, _ = seeded_failed_job
    headers = await _seed_admin_headers(db_session, email="requeue-missing-admin@example.com")

    storage_client.cookies.clear()
    response = await storage_client.post(
        f"{API}/videos/{video_id}/processing/requeue",
        json={"stage": "detection"},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVALID_VIDEO_STATE


@pytest.mark.asyncio(loop_scope="session")
async def test_requeue_rejects_unauthenticated(storage_client: AsyncClient) -> None:
    storage_client.cookies.clear()
    response = await storage_client.post(
        f"{API}/videos/{uuid.uuid4()}/processing/requeue",
        json={"stage": "transcode"},
    )
    assert response.status_code == 401
