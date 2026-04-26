"""Integration smoke: one narrative that exercises the full vertical slice.

This single test is intentionally long and linear — it is the closest thing
in the suite to a "a user from scratch" demo, and is the one place where all
the moving parts have to line up in the same order as production:

    1. Register (cookie-only; *no* tokens in the JSON body)
    2. Create team + game
    3. Initiate video upload and complete it (PUT presign path)
    4. Drive the worker's transcode runtime end to end (claim → run → done)
    5. Pull the video detail — video is PROCESSED and a playback token exists
    6. Hit /videos/{id}/playback/verify — the live session accepts the token
    7. Logout — session_version rotates
    8. Re-login as the same user
    9. The same playback token now fails verification because session_version changed

The other suites cover micro-behaviors; this one catches the regressions that
only surface when those layers are wired together.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from nextballup_api.routers.videos import get_storage
from nextballup_api.storage import PresignedPart, PresignedUpload, StoragePresigner
from nextballup_worker.runtime import execute_transcode
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.enums import UploadMethod

API = "/api/v1"


class _FakeE2EStorage:
    """Minimal presigner: enough for a PUT-sized upload and a GET playback URL.

    Multipart support is intentionally minimal — we don't stitch parts together
    or hash anything; we just record that complete_multipart was called so the
    narrative test can assert the router stitched + closed the multipart flow.
    """

    def __init__(self) -> None:
        self.object_sizes: dict[str, int] = {}
        self.object_metadata: dict[str, dict[str, str]] = {}
        self.multipart_uploads: dict[str, str] = {}
        self.multipart_completions: list[dict[str, Any]] = []

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
        self.object_sizes[key] = file_size_bytes
        return PresignedUpload(
            method=UploadMethod.PUT,
            url=f"https://fake-storage.test/{key}",
            headers={"Content-Type": content_type},
        )

    def create_multipart(
        self, *, key: str, content_type: str, file_size_bytes: int, num_parts: int
    ) -> PresignedUpload:
        """Dedicated helper used by the multipart narrative — the production
        presigner chooses PUT vs. MULTIPART inside presign_upload based on
        settings; the fake lets the test drive the choice directly."""
        upload_id = f"fake-mpid-{len(self.multipart_uploads)}"
        self.object_sizes[key] = file_size_bytes
        self.multipart_uploads[key] = upload_id
        part_size = max(1, (file_size_bytes + num_parts - 1) // num_parts)
        parts = tuple(
            PresignedPart(
                part_number=i,
                url=f"https://fake-storage.test/{key}?partNumber={i}",
            )
            for i in range(1, num_parts + 1)
        )
        return PresignedUpload(
            method=UploadMethod.MULTIPART,
            upload_id=upload_id,
            parts=parts,
            part_size_bytes=part_size,
        )

    def complete_multipart(self, *, key: str, upload_id: str, parts: list[dict[str, Any]]) -> None:
        self.multipart_completions.append(
            {"key": key, "upload_id": upload_id, "parts": list(parts)}
        )

    def abort_multipart(self, *, key: str, upload_id: str) -> None:  # pragma: no cover -- not used
        raise AssertionError("multipart abort path not exercised by this smoke")

    def delete_object(self, *, key: str) -> None:
        self.object_sizes.pop(key, None)

    def head_object(self, *, key: str) -> dict[str, Any] | None:
        size = self.object_sizes.get(key)
        if size is None:
            return None
        etag = (key.encode("utf-8").hex().ljust(32, "0"))[:32]
        return {
            "ContentLength": size,
            "ETag": f'"{etag}"',
            "Metadata": self.object_metadata.get(key, {}),
        }

    def presign_get(
        self, *, key: str, expires_in: int, response_content_type: str | None = None
    ) -> str:
        return f"https://fake-storage.test/{key}?X-Get=1&exp={expires_in}"

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


@pytest_asyncio.fixture(loop_scope="session")
async def _smoke_storage() -> _FakeE2EStorage:
    return _FakeE2EStorage()


@pytest_asyncio.fixture(loop_scope="session")
async def _smoke_client(
    db_session: AsyncSession, _smoke_storage: _FakeE2EStorage
) -> AsyncIterator[AsyncClient]:
    from nextballup_api.deps import get_db
    from nextballup_api.main import app

    from nextballup_core.settings import reload_settings
    from tests.csrf_helper import make_csrf_mirror_hook

    reload_settings()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_storage() -> StoragePresigner:
        # One shared fake so upload size recorded in presign_upload is visible
        # to the subsequent head_object call during /videos/{id}/complete.
        return _smoke_storage

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


@pytest.mark.asyncio(loop_scope="session")
async def test_end_to_end_smoke(
    _smoke_client: AsyncClient,
    _smoke_storage: _FakeE2EStorage,
    db_session: AsyncSession,
) -> None:
    client = _smoke_client
    coach_email = "e2e-coach@example.com"

    # ---- 1. Register (cookie-only contract) -------------------------------
    register = await client.post(
        f"{API}/auth/register",
        json={
            "email": coach_email,
            "password": "Password1!",
            "full_name": "E2E Coach",
            "role": "coach",
        },
    )
    assert register.status_code == 201
    body = register.json()
    assert "access_token" not in body
    assert "refresh_token" not in body
    # Auth + CSRF cookies are the transport.
    assert register.cookies.get("nbu_access_token")
    assert register.cookies.get("nbu_refresh_token")
    assert register.cookies.get("nbu_csrf_token")

    # ---- 2. Team + game ---------------------------------------------------
    team_resp = await client.post(
        f"{API}/teams",
        json={
            "name": "Smoke Varsity",
            "sport": "basketball",
            "level": "high_school",
            "institution": "Smoke High",
            "institution_type": "k12_school",
            "season": "2026-2027",
        },
    )
    assert team_resp.status_code == 201, team_resp.text
    team = team_resp.json()

    game_resp = await client.post(
        f"{API}/games",
        json={
            "team_id": team["id"],
            "opponent_name": "Ember",
            "game_type": "regular_season",
            "date": "2026-11-15",
            "location": "Smoke Gym",
            "is_home": True,
            "periods": 4,
            "period_length_minutes": 8,
        },
    )
    assert game_resp.status_code == 201, game_resp.text
    game = game_resp.json()

    # ---- 3. Upload + complete --------------------------------------------
    upload_resp = await client.post(
        f"{API}/videos/upload",
        json={
            "game_id": game["id"],
            "filename": "smoke_q1.mp4",
            "file_size_bytes": 250 * 1024 * 1024,
            "content_type": "video/mp4",
            "camera_position": "sideline",
            "camera_height": "elevated",
        },
    )
    assert upload_resp.status_code == 201, upload_resp.text
    upload = upload_resp.json()
    assert upload["upload_method"] == "PUT"

    complete_resp = await client.post(
        f"{API}/videos/{upload['id']}/complete",
        json={"checksum_sha256": "a" * 64},
    )
    assert complete_resp.status_code == 200, complete_resp.text
    complete = complete_resp.json()
    job_id = uuid.UUID(complete["job_id"])
    video_id = uuid.UUID(complete["id"])

    # ---- 4. Drive the worker transcode runtime ---------------------------
    # Uses the real runtime function against the live DB session — exercises
    # the RLS policies, audit writes, video-status transition, and job
    # termination all in one call. No Celery broker required.
    result = await execute_transcode(
        db_session,
        job_id=job_id,
        celery_task_id="smoke-task-1",
        storage=_smoke_storage,
    )
    assert result.status == "completed", result

    # Transcode may have committed on its own; force the session to see the
    # latest state.
    db_session.sync_session.expunge_all()

    # ---- 5. Video detail shows PROCESSED + playback token ----------------
    detail_resp = await client.get(f"{API}/videos/{video_id}")
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert detail["status"] == "processed"
    assert detail["processing"]["transcode"] == "completed"
    playback_token = detail.get("playback_token")
    assert playback_token, "processed video detail must issue a playback token"
    assert detail["playback_url"] and "/mezzanine/" in detail["playback_url"]

    # ---- 6. /playback/verify accepts the live token ----------------------
    verify_resp = await client.post(
        f"{API}/videos/{video_id}/playback/verify",
        json={"token": playback_token},
    )
    assert verify_resp.status_code == 200, verify_resp.text
    assert verify_resp.json()["video_id"] == str(video_id)

    # ---- 7. Logout rotates session_version -------------------------------
    logout_resp = await client.post(f"{API}/auth/logout")
    assert logout_resp.status_code == 204

    # ---- 8. Re-authenticate as the same user -----------------------------
    login_resp = await client.post(
        f"{API}/auth/login",
        json={"email": coach_email, "password": "Password1!"},
    )
    assert login_resp.status_code == 200, login_resp.text

    # ---- 9. Previously valid playback token now rejected -----------------
    # This is the real session-version revocation check: the caller is
    # authenticated again, but the playback token was minted before logout.
    post_logout = await client.post(
        f"{API}/videos/{video_id}/playback/verify",
        json={"token": playback_token},
    )
    assert post_logout.status_code == 401


@pytest.mark.asyncio(loop_scope="session")
async def test_end_to_end_multipart_with_client_checksum(
    _smoke_client: AsyncClient,
    _smoke_storage: _FakeE2EStorage,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Companion narrative: a multipart upload with a client-attested SHA-256.

    The single-PUT smoke above already exercises the full auth / transcode /
    playback / session-version loop. This one focuses on the multipart path —
    multiple ETags echoed back to /complete, the router actually closing the
    multipart upload with storage, and the checksum_sha256 field surviving
    from client → /complete → video record → audit log.
    """
    from nextballup_api.routers import videos as videos_router
    from sqlalchemy import select

    from nextballup_core.settings import reload_settings
    from nextballup_db.models.audit import AuditLog
    from nextballup_db.models.video import Video

    client = _smoke_client
    coach_email = "e2e-multipart@example.com"

    # Shrink the multipart threshold so we don't have to claim a 1.5 GB upload.
    monkeypatch.setenv("UPLOAD_MULTIPART_THRESHOLD_BYTES", "1048576")  # 1 MB
    monkeypatch.setenv("UPLOAD_MULTIPART_PART_SIZE_BYTES", "1048576")
    reload_settings()

    # Re-point the videos router's presign_upload helper at the fake's
    # create_multipart path for any upload above the shrunken threshold. We
    # have to monkeypatch the live presigner's presign_upload because the
    # production implementation would call real boto3 multipart APIs.
    original_presign_upload = _smoke_storage.presign_upload

    def _routing_presign_upload(
        *,
        key: str,
        content_type: str,
        file_size_bytes: int,
        checksum_sha256: str | None = None,
    ) -> PresignedUpload:
        if file_size_bytes > 1_048_576:
            num_parts = max(1, (file_size_bytes + 1_048_575) // 1_048_576)
            return _smoke_storage.create_multipart(
                key=key,
                content_type=content_type,
                file_size_bytes=file_size_bytes,
                num_parts=num_parts,
            )
        return original_presign_upload(
            key=key,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            checksum_sha256=checksum_sha256,
        )

    monkeypatch.setattr(_smoke_storage, "presign_upload", _routing_presign_upload)

    # ---- register + team + game (short version) -------------------------
    register = await client.post(
        f"{API}/auth/register",
        json={
            "email": coach_email,
            "password": "Password1!",
            "full_name": "Multipart Coach",
            "role": "coach",
        },
    )
    assert register.status_code == 201
    team_resp = await client.post(
        f"{API}/teams",
        json={
            "name": "Multipart Varsity",
            "sport": "basketball",
            "level": "high_school",
            "institution": "Smoke High",
            "institution_type": "k12_school",
            "season": "2026-2027",
        },
    )
    assert team_resp.status_code == 201, team_resp.text
    game_resp = await client.post(
        f"{API}/games",
        json={
            "team_id": team_resp.json()["id"],
            "opponent_name": "Ember",
            "game_type": "regular_season",
            "date": "2026-11-15",
            "location": "Smoke Gym",
            "is_home": True,
            "periods": 4,
            "period_length_minutes": 8,
        },
    )
    assert game_resp.status_code == 201, game_resp.text

    # ---- request a multipart upload (file > 1 MB) -----------------------
    # We claim 4 MB so the backend response carries multiple parts even after
    # the threshold shrink.
    file_size = 4 * 1024 * 1024
    upload_resp = await client.post(
        f"{API}/videos/upload",
        json={
            "game_id": game_resp.json()["id"],
            "filename": "smoke_multipart.mp4",
            "file_size_bytes": file_size,
            "content_type": "video/mp4",
            "camera_position": "sideline",
            "camera_height": "elevated",
        },
    )
    assert upload_resp.status_code == 201, upload_resp.text
    upload = upload_resp.json()
    assert upload["upload_method"] == "MULTIPART", upload
    assert upload["part_urls"], "multipart response must include part URLs"
    assert upload["upload_id"], "multipart response must include an upload id"

    # ---- /complete with every part ETag + a client checksum -------------
    client_checksum = "a" * 64  # valid 64-char hex attestation
    part_payload = [
        {"part_number": part["part_number"], "etag": f"mp-etag-{part['part_number']}"}
        for part in upload["part_urls"]
    ]
    complete_resp = await client.post(
        f"{API}/videos/{upload['id']}/complete",
        json={
            "parts": part_payload,
            "checksum_sha256": client_checksum,
        },
    )
    assert complete_resp.status_code == 200, complete_resp.text
    complete = complete_resp.json()
    video_id = uuid.UUID(complete["id"])

    # ---- the fake presigner observed a complete_multipart call ----------
    assert _smoke_storage.multipart_completions, "router should close the mpu"
    last_completion = _smoke_storage.multipart_completions[-1]
    assert last_completion["upload_id"] == upload["upload_id"]
    assert len(last_completion["parts"]) == len(part_payload)

    # ---- the video record and audit log carry the client's checksum -----
    db_session.sync_session.expunge_all()
    video = (await db_session.execute(select(Video).where(Video.id == video_id))).scalar_one()
    assert video.checksum_sha256 == client_checksum
    audit_rows = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.resource_id == video_id)
                .where(AuditLog.action == "videos.upload.complete")
            )
        )
        .scalars()
        .all()
    )
    assert audit_rows, "VIDEO_UPLOAD_COMPLETED audit entry must exist"
    assert any((row.extra or {}).get("checksum_sha256") == client_checksum for row in audit_rows), (
        "audit extra must echo the attested checksum"
    )

    # Leave the caller logged in — no playback/session assertions here; the
    # sibling narrative already covers that loop.
    _ = videos_router  # re-export guard (keeps the import visible to linters)
