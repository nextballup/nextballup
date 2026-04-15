from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from nextballup_api.routers.videos import get_storage
from nextballup_api.security.jwt import create_access_token
from nextballup_api.security.passwords import hash_password
from nextballup_api.storage import (
    PresignedPart,
    PresignedUpload,
    StorageFailureError,
    StoragePresigner,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import (
    ProcessingJobStage,
    ProcessingJobStatus,
    UploadMethod,
    UserRole,
    VideoStatus,
)
from nextballup_core.settings import get_settings
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.user import User
from nextballup_db.models.video import ProcessingJob, Video

API = "/api/v1"


# ---- Fake storage ---------------------------------------------------------


class FakeStorage:
    def __init__(self) -> None:
        self.completed_multiparts: list[dict[str, Any]] = []
        self.aborted_multiparts: list[dict[str, Any]] = []
        self.object_sizes: dict[str, int] = {}
        self.pending_multiparts: dict[str, tuple[str, int]] = {}
        self.fail_presign = False
        self.fail_complete = False
        self.fail_head_for_keys: set[str] = set()

    def is_configured(self) -> bool:
        return True

    def presign_upload(
        self, *, key: str, content_type: str, file_size_bytes: int
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
        upload_id = f"fake-upload-{uuid.uuid4().hex[:8]}"
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

    def head_object(self, *, key: str) -> dict[str, Any] | None:
        if key in self.fail_head_for_keys:
            return None
        size = self.object_sizes.get(key)
        if size is None:
            return None
        return {"ContentLength": size}

    def presign_get(
        self, *, key: str, expires_in: int, response_content_type: str | None = None
    ) -> str:
        ct_param = f"&rct={response_content_type}" if response_content_type else ""
        return f"https://fake-storage.test/{key}?X-Get=1&exp={expires_in}{ct_param}"


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

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_get_storage() -> StoragePresigner:
        return fake_storage

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = _override_get_storage
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
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

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_get_storage() -> StoragePresigner | None:
        return None

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = _override_get_storage
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
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
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "g" * 64}
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
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "j" * 64}
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
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "h" * 64}
    )

    status_response = await storage_client.get(f"{API}/videos/{video_id}/status")
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "queued"
    assert body["stages"]["transcode"]["status"] == "pending"


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
        f"{API}/videos/{video_id}/complete", json={"checksum_sha256": "i" * 64}
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
