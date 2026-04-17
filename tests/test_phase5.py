"""Phase 5 tests: signed playback delivery + game list/patch + worker outputs.

Reuses the Phase 4 storage_client/fake_storage fixtures from test_worker.py via
a small import dance — pytest discovers them through the shared conftest, but
since they're declared at module scope in test_worker we re-create local
copies here to keep this module self-contained.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from nextballup_api.routers.videos import get_storage
from nextballup_api.security.jwt import decode_token
from nextballup_api.storage import PresignedPart, PresignedUpload, StoragePresigner
from nextballup_worker.runtime import execute_transcode
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import GameStatus, UploadMethod, VideoStatus
from nextballup_core.settings import get_settings
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.video import Video

API = "/api/v1"


# ---- Fake storage (mirror of test_worker version) ------------------------


class FakeStorage:
    def __init__(self) -> None:
        self.object_sizes: dict[str, int] = {}
        self.pending_multiparts: dict[str, tuple[str, int]] = {}
        self.aborted_multiparts: list[dict[str, str]] = []
        self.completed_multiparts: list[dict[str, Any]] = []
        self.presigned_get_calls: list[dict[str, Any]] = []

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
        size = self.object_sizes.get(key)
        if size is None:
            return None
        synthetic_md5 = (key.encode("utf-8").hex().ljust(32, "0"))[:32]
        return {"ContentLength": size, "ETag": f'"{synthetic_md5}"'}

    def presign_get(
        self, *, key: str, expires_in: int, response_content_type: str | None = None
    ) -> str:
        self.presigned_get_calls.append(
            {"key": key, "expires_in": expires_in, "ct": response_content_type}
        )
        return f"https://fake-storage.test/{key}?GET=1&exp={expires_in}"

    def download_file(self, *, key: str, destination: str) -> None:
        Path(destination).write_bytes(b"fake-video")

    def upload_file(self, *, key: str, source: str, content_type: str) -> None:
        self.object_sizes[key] = Path(source).stat().st_size


@pytest_asyncio.fixture(loop_scope="session")
async def fake_storage() -> FakeStorage:
    return FakeStorage()


@pytest_asyncio.fixture(loop_scope="session")
async def storage_client(
    db_session: AsyncSession, fake_storage: FakeStorage
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


# ---- Helpers --------------------------------------------------------------


def _coach(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "password": "Password1!",
        "full_name": "Mike Johnson",
        "role": "coach",
    }


def _player(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "password": "Password1!",
        "full_name": "James Williams",
        "role": "player",
    }


def _team_body(name: str = "Lincoln Varsity") -> dict[str, Any]:
    return {
        "name": name,
        "sport": "basketball",
        "level": "high_school",
        "institution": "Lincoln High School",
        "institution_type": "k12_school",
        "season": "2026-2027",
    }


def _game_body(team_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
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
    body.update(overrides)
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


async def _seed_processed_video(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    *,
    coach_email: str,
    upload_overrides: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (team_id, video_id, transcode_job_id) — video is PROCESSED."""
    await client.post(f"{API}/auth/register", json=_coach(coach_email))
    team = (await client.post(f"{API}/teams", json=_team_body())).json()
    game = (await client.post(f"{API}/games", json=_game_body(team["id"]))).json()
    upload = (
        await client.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"], **(upload_overrides or {})),
        )
    ).json()
    complete = (
        await client.post(
            f"{API}/videos/{upload['id']}/complete",
            json={"checksum_sha256": "a" * 64},
        )
    ).json()

    result = await execute_transcode(
        db_session,
        job_id=uuid.UUID(complete["job_id"]),
        celery_task_id="celery-phase5",
        storage=fake_storage,
    )
    assert result.status == "completed"
    return (
        uuid.UUID(team["id"]),
        uuid.UUID(complete["id"]),
        uuid.UUID(complete["job_id"]),
    )


# ---- Worker output materialization ---------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_worker_persists_output_keys_and_etag(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
) -> None:
    team_id, video_id, _ = await _seed_processed_video(
        storage_client, db_session, fake_storage, coach_email="outputs-coach@example.com"
    )

    video = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert video is not None
    assert video.status is VideoStatus.PROCESSED
    assert video.storage_key_mezzanine is not None
    assert video.storage_key_mezzanine != video.storage_key_raw
    assert video.storage_etag is not None
    assert "-" not in video.storage_etag  # single-part MD5-shape
    assert video.codec == "h264"
    assert video.duration_seconds == 42.0

    # Audit row for output materialization is present and tied to the team
    materialized = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_OUTPUT_MATERIALIZED,
            AuditLog.team_id == team_id,
        )
    )
    assert materialized == 1


# ---- Playback URL + token -----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_processed_video_returns_signed_playback(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
) -> None:
    team_id, video_id, _ = await _seed_processed_video(
        storage_client, db_session, fake_storage, coach_email="play-coach@example.com"
    )

    response = await storage_client.get(f"{API}/videos/{video_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == VideoStatus.PROCESSED.value
    assert body["playback_url"]
    assert body["playback_url"].startswith("https://fake-storage.test/")
    assert "/mezzanine/" in body["playback_url"]
    assert body["playback_token"]
    assert body["playback_format"] == "mp4"
    assert body["token_expires_at"] is not None
    assert body["storage_etag"] is not None

    # Token decodes with the playback audience, references this video
    settings = get_settings()
    decoded = decode_token(
        body["playback_token"],
        expected_type="playback",
        audience=settings.playback_token_audience,
    )
    assert decoded["vid"] == str(video_id)
    assert decoded["tid"] == str(team_id)
    assert decoded["aud"] == settings.playback_token_audience
    assert fake_storage.presigned_get_calls[-1]["expires_in"] == min(
        settings.playback_url_expires_seconds,
        settings.playback_token_expire_seconds,
    )

    issued = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_PLAYBACK_ISSUED,
            AuditLog.team_id == team_id,
        )
    )
    assert issued and issued >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_processed_mov_returns_signed_playback_from_mezzanine(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
) -> None:
    _, video_id, _ = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="play-mov@example.com",
        upload_overrides={
            "filename": "iphone_clip.mov",
            "content_type": "video/quicktime",
        },
    )

    response = await storage_client.get(f"{API}/videos/{video_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == VideoStatus.PROCESSED.value
    assert body["playback_url"] is not None
    assert "/mezzanine/" in body["playback_url"]
    assert body["playback_token"] is not None
    assert body["playback_format"] == "mp4"


@pytest.mark.asyncio(loop_scope="session")
async def test_pending_video_does_not_issue_playback(
    storage_client: AsyncClient,
) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("pending-play@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()
    upload = (
        await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    ).json()

    response = await storage_client.get(f"{API}/videos/{upload['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == VideoStatus.PENDING_UPLOAD.value
    assert body["playback_url"] is None
    assert body["playback_token"] is None
    assert body["playback_format"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_non_member_cannot_access_processed_video(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
) -> None:
    _, video_id, _ = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="play-owner@example.com",
    )
    # Snooping coach from another tenant
    await storage_client.post(f"{API}/auth/register", json=_coach("play-snoop@example.com"))
    response = await storage_client.get(f"{API}/videos/{video_id}")
    assert response.status_code in {403, 404}


@pytest.mark.asyncio(loop_scope="session")
async def test_playback_token_expires_within_configured_window(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
) -> None:
    _, video_id, _ = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="play-expiry@example.com",
    )
    response = await storage_client.get(f"{API}/videos/{video_id}")
    body = response.json()

    expires_at = datetime.fromisoformat(body["token_expires_at"])
    now = datetime.now(tz=UTC)
    settings = get_settings()
    # Token TTL is bounded; allow a small clock-skew window in either direction
    assert expires_at > now
    assert expires_at <= now + timedelta(seconds=settings.playback_token_expire_seconds + 5)


@pytest.mark.asyncio(loop_scope="session")
async def test_playback_url_uses_hls_when_available(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
) -> None:
    _, video_id, _ = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="hls-coach@example.com",
    )
    # Promote the video to a real HLS output to verify the preference order
    video = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert video is not None
    video.storage_key_hls = f"hls/{video.team_id}/{video.id}/manifest.m3u8"
    fake_storage.object_sizes[video.storage_key_hls] = video.file_size_bytes or 1
    await db_session.commit()

    response = await storage_client.get(f"{API}/videos/{video_id}")
    body = response.json()
    assert body["playback_format"] == "hls"
    assert "manifest.m3u8" in body["playback_url"]


@pytest.mark.asyncio(loop_scope="session")
async def test_playback_falls_back_to_mezzanine_when_hls_key_is_stale(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
) -> None:
    _, video_id, _ = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="hls-stale@example.com",
    )
    video = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert video is not None
    video.storage_key_hls = f"hls/{video.team_id}/{video.id}/manifest.m3u8"
    await db_session.commit()

    response = await storage_client.get(f"{API}/videos/{video_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["playback_format"] == "mp4"
    assert body["playback_url"] is not None
    assert str(video.id) in body["playback_url"]
    assert "manifest.m3u8" not in body["playback_url"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_playback_object_returns_no_playback_fields_and_no_issue_audit(
    storage_client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
) -> None:
    team_id, video_id, _ = await _seed_processed_video(
        storage_client,
        db_session,
        fake_storage,
        coach_email="playback-missing@example.com",
    )
    video = await db_session.scalar(select(Video).where(Video.id == video_id))
    assert video is not None
    assert video.storage_key_mezzanine is not None
    fake_storage.object_sizes.pop(video.storage_key_mezzanine, None)

    issued_before = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_PLAYBACK_ISSUED,
            AuditLog.team_id == team_id,
        )
    )

    response = await storage_client.get(f"{API}/videos/{video_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == VideoStatus.PROCESSED.value
    assert body["playback_url"] is None
    assert body["playback_token"] is None
    assert body["playback_format"] is None
    assert body["token_expires_at"] is None

    issued_after = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.VIDEO_PLAYBACK_ISSUED,
            AuditLog.team_id == team_id,
        )
    )
    assert issued_after == issued_before


# ---- GET /games + PATCH /games/{id} -------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_list_games_returns_user_team_games(
    storage_client: AsyncClient,
) -> None:
    coach = _coach("list-coach@example.com")
    await storage_client.post(f"{API}/auth/register", json=coach)
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    g1 = (
        await storage_client.post(f"{API}/games", json=_game_body(team["id"], date="2026-10-01"))
    ).json()
    g2 = (
        await storage_client.post(
            f"{API}/games",
            json=_game_body(team["id"], date="2026-12-01", opponent_name="Other"),
        )
    ).json()

    response = await storage_client.get(f"{API}/games?team_id={team['id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["per_page"] == 20
    assert body["has_next"] is False
    ids = {g["id"] for g in body["games"]}
    assert ids == {g1["id"], g2["id"]}


@pytest.mark.asyncio(loop_scope="session")
async def test_list_games_filters_by_status_and_type(
    storage_client: AsyncClient,
) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("filter-coach@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    g_regular = (
        await storage_client.post(
            f"{API}/games",
            json=_game_body(team["id"], game_type="regular_season"),
        )
    ).json()
    await storage_client.post(
        f"{API}/games",
        json=_game_body(team["id"], game_type="practice", opponent_name=None),
    )

    response = await storage_client.get(
        f"{API}/games?team_id={team['id']}&game_type=regular_season"
    )
    body = response.json()
    assert body["total"] == 1
    assert body["games"][0]["id"] == g_regular["id"]


@pytest.mark.asyncio(loop_scope="session")
async def test_list_games_filters_by_date_range(
    storage_client: AsyncClient,
) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("date-coach@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    g_in = (
        await storage_client.post(f"{API}/games", json=_game_body(team["id"], date="2026-11-15"))
    ).json()
    await storage_client.post(f"{API}/games", json=_game_body(team["id"], date="2027-01-15"))

    response = await storage_client.get(
        f"{API}/games?team_id={team['id']}&from=2026-11-01&to=2026-11-30"
    )
    body = response.json()
    assert body["total"] == 1
    assert body["games"][0]["id"] == g_in["id"]


@pytest.mark.asyncio(loop_scope="session")
async def test_list_games_excludes_other_teams(
    storage_client: AsyncClient,
) -> None:
    coach_a = _coach("list-iso-a@example.com")
    await storage_client.post(f"{API}/auth/register", json=coach_a)
    team_a = (await storage_client.post(f"{API}/teams", json=_team_body("A"))).json()
    await storage_client.post(f"{API}/games", json=_game_body(team_a["id"]))

    await storage_client.post(f"{API}/auth/register", json=_coach("list-iso-b@example.com"))
    team_b = (await storage_client.post(f"{API}/teams", json=_team_body("B"))).json()
    await storage_client.post(f"{API}/games", json=_game_body(team_b["id"]))

    # Coach B (last logged-in via cookies) should only see team B's games when
    # no team filter is applied.
    response = await storage_client.get(f"{API}/games")
    body = response.json()
    assert body["total"] == 1
    assert body["games"][0]["team_id"] == team_b["id"]


@pytest.mark.asyncio(loop_scope="session")
async def test_list_games_blocks_non_member_team_filter(
    storage_client: AsyncClient,
) -> None:
    # Owner creates team
    await storage_client.post(f"{API}/auth/register", json=_coach("nonmember-owner@example.com"))
    team_a = (await storage_client.post(f"{API}/teams", json=_team_body())).json()

    # Snooping coach asks for team_a's games via filter
    await storage_client.post(f"{API}/auth/register", json=_coach("nonmember-snoop@example.com"))
    response = await storage_client.get(f"{API}/games?team_id={team_a['id']}")
    assert response.status_code in {403, 404}


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_game_updates_scoreboard_and_audits(
    storage_client: AsyncClient, db_session: AsyncSession
) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("patch-coach@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    response = await storage_client.patch(
        f"{API}/games/{game['id']}",
        json={
            "score_team": 67,
            "score_opponent": 54,
            "status": "completed",
            "notes": "Tight game in Q4",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["score_team"] == 67
    assert body["score_opponent"] == 54
    assert body["status"] == GameStatus.COMPLETED.value
    assert body["notes"] == "Tight game in Q4"

    audited = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.GAME_UPDATED,
            AuditLog.team_id == uuid.UUID(team["id"]),
        )
    )
    assert audited == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_game_rejects_non_coach(storage_client: AsyncClient) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("patch-owner@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    await storage_client.post(f"{API}/auth/register", json=_player("patch-player@example.com"))
    join = await storage_client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 8},
    )
    assert join.status_code == 200

    response = await storage_client.patch(f"{API}/games/{game['id']}", json={"score_team": 12})
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_game_blocks_terminal_status_transition(
    storage_client: AsyncClient,
) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("term-coach@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    # Mark completed
    completed = await storage_client.patch(
        f"{API}/games/{game['id']}", json={"status": "completed"}
    )
    assert completed.status_code == 200

    # Try to flip back to scheduled — coach should be blocked
    response = await storage_client.patch(f"{API}/games/{game['id']}", json={"status": "scheduled"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.GAME_TERMINAL_STATUS


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_game_allows_same_terminal_status_with_other_updates(
    storage_client: AsyncClient,
) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("term-same@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    completed = await storage_client.patch(
        f"{API}/games/{game['id']}",
        json={"status": "completed", "score_team": 70, "score_opponent": 62},
    )
    assert completed.status_code == 200

    response = await storage_client.patch(
        f"{API}/games/{game['id']}",
        json={"status": "completed", "notes": "Final score confirmed after stat review"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == GameStatus.COMPLETED.value
    assert body["notes"] == "Final score confirmed after stat review"


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_game_isolation_other_team_returns_403_or_404(
    storage_client: AsyncClient,
) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("p-iso-a@example.com"))
    team_a = (await storage_client.post(f"{API}/teams", json=_team_body("A"))).json()
    game_a = (await storage_client.post(f"{API}/games", json=_game_body(team_a["id"]))).json()

    await storage_client.post(f"{API}/auth/register", json=_coach("p-iso-b@example.com"))
    response = await storage_client.patch(f"{API}/games/{game_a['id']}", json={"score_team": 99})
    assert response.status_code in {403, 404}


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_game_validation_rejects_unknown_field(
    storage_client: AsyncClient,
) -> None:
    await storage_client.post(f"{API}/auth/register", json=_coach("validate-patch@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    response = await storage_client.patch(
        f"{API}/games/{game['id']}", json={"made_up_field": "nope"}
    )
    assert response.status_code == 422
