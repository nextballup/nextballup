"""Adversarial end-to-end tests.

These tests target the seams most interesting to a motivated attacker —
cross-tenant IDOR, CSRF bypass, membership-tier privilege escalation, and
admin-endpoint misuse. Each test mimics a specific abuse pattern a
penetration tester would probe: can I poke a resource I don't own? Can I
skip the double-submit? Can a coach pull admin-only levers?

Where the routers-layer already covers a path (e.g., the video detail
endpoint's cross-tenant 404), we skip duplication here and focus on the
less-obvious surfaces. The happy-path / positive-case coverage lives in
the per-domain test files.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from nextballup_api.routers.videos import get_storage
from nextballup_api.storage import PresignedUpload, StoragePresigner

from nextballup_core.constants import ErrorCode
from nextballup_core.enums import UploadMethod

API = "/api/v1"


# ---- Minimal storage fake (shared with other tests' shape) ----------------


class _FakeStorage:
    def __init__(self) -> None:
        self.object_sizes: dict[str, int] = {}
        self.object_metadata: dict[str, dict[str, str]] = {}

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

    def complete_multipart(self, *, key: str, upload_id: str, parts: list[dict[str, Any]]) -> None:
        return None

    def abort_multipart(self, *, key: str, upload_id: str) -> None:
        return None

    def delete_object(self, *, key: str) -> None:
        self.object_sizes.pop(key, None)

    def head_object(self, *, key: str) -> dict[str, Any] | None:
        size = self.object_sizes.get(key)
        if size is None:
            return None
        return {"ContentLength": size, "Metadata": self.object_metadata.get(key, {})}

    def presign_get(
        self, *, key: str, expires_in: int, response_content_type: str | None = None
    ) -> str:
        return f"https://fake-storage.test/{key}?exp={expires_in}"

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
async def storage_client(db_session: Any) -> AsyncIterator[AsyncClient]:
    from nextballup_api.deps import get_db
    from nextballup_api.main import app

    from nextballup_core.settings import reload_settings
    from tests.csrf_helper import make_csrf_mirror_hook

    reload_settings()

    async def _override_get_db() -> AsyncIterator[Any]:
        yield db_session

    def _override_storage() -> StoragePresigner:
        return cast("StoragePresigner", _FakeStorage())

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


# ---- Payload builders -----------------------------------------------------


def _coach_payload(email: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "email": email,
        "password": "Password1!",
        "full_name": "Abuse Coach",
        "role": "coach",
    }
    body.update(overrides)
    return body


def _player_payload(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "password": "Password1!",
        "full_name": "Abuse Player",
        "role": "player",
    }


def _team_body(name: str = "Lincoln Varsity") -> dict[str, Any]:
    return {
        "name": name,
        "sport": "basketball",
        "level": "high_school",
        "institution": "Lincoln High",
        "institution_type": "k12_school",
        "season": "2026-27",
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
        "notes": "",
    }


def _upload_body(game_id: str) -> dict[str, Any]:
    return {
        "game_id": game_id,
        "filename": "lincoln.mp4",
        "file_size_bytes": 250 * 1024 * 1024,
        "content_type": "video/mp4",
        "camera_position": "sideline",
        "camera_height": "elevated",
    }


async def _register(client: AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


async def _seed_team_with_video(
    client: AsyncClient, *, coach_email: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Register a coach, create their team+game+video, return (team, game, video)."""
    await _register(client, _coach_payload(coach_email))
    team = (await client.post(f"{API}/teams", json=_team_body())).json()
    game = (await client.post(f"{API}/games", json=_game_body(team["id"]))).json()
    video = (await client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))).json()
    return team, game, video


# ---- Cross-tenant IDOR ----------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_cross_tenant_complete_is_denied(storage_client: AsyncClient) -> None:
    """Coach B must not be able to /complete coach A's in-flight video.

    The complete handler loads-for-update by video_id only; this test confirms
    the tenant guard still fires after that lookup."""
    _, _, video_a = await _seed_team_with_video(
        storage_client, coach_email="idor-complete-a@example.com"
    )
    await _register(storage_client, _coach_payload("idor-complete-b@example.com"))

    response = await storage_client.post(
        f"{API}/videos/{video_a['id']}/complete",
        json={"checksum_sha256": "b" * 64},
    )
    assert response.status_code in {403, 404}


@pytest.mark.asyncio(loop_scope="session")
async def test_cross_tenant_status_is_denied(storage_client: AsyncClient) -> None:
    _, _, video_a = await _seed_team_with_video(
        storage_client, coach_email="idor-status-a@example.com"
    )
    await _register(storage_client, _coach_payload("idor-status-b@example.com"))

    response = await storage_client.get(f"{API}/videos/{video_a['id']}/status")
    assert response.status_code in {403, 404}


@pytest.mark.asyncio(loop_scope="session")
async def test_cross_tenant_game_videos_list_is_denied(storage_client: AsyncClient) -> None:
    """A non-member must not be able to enumerate videos on another team's
    game — the 404/403 shape masks existence."""
    _, game_a, _ = await _seed_team_with_video(
        storage_client, coach_email="idor-list-a@example.com"
    )
    await _register(storage_client, _coach_payload("idor-list-b@example.com"))

    response = await storage_client.get(f"{API}/games/{game_a['id']}/videos")
    assert response.status_code in {403, 404}


@pytest.mark.asyncio(loop_scope="session")
async def test_cross_tenant_game_patch_is_denied(storage_client: AsyncClient) -> None:
    _, game_a, _ = await _seed_team_with_video(
        storage_client, coach_email="idor-patch-a@example.com"
    )
    await _register(storage_client, _coach_payload("idor-patch-b@example.com"))

    response = await storage_client.patch(
        f"{API}/games/{game_a['id']}", json={"score_team": 99, "score_opponent": 0}
    )
    assert response.status_code in {403, 404}


@pytest.mark.asyncio(loop_scope="session")
async def test_requeue_rejects_non_failed_video_for_team_coach(
    storage_client: AsyncClient,
) -> None:
    """Team coaches can retry failed alpha transcodes, but the endpoint must
    still reject active/non-failed uploads instead of creating duplicate work."""
    _, _, video = await _seed_team_with_video(
        storage_client, coach_email="idor-requeue@example.com"
    )
    # Coach cookies still on the client.
    response = await storage_client.post(
        f"{API}/videos/{video['id']}/processing/requeue",
        json={"stage": "transcode"},
    )
    assert response.status_code == 409


# ---- Role-tier escalation -------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_player_member_cannot_initiate_upload(storage_client: AsyncClient) -> None:
    """A player who legitimately joined the team must not be able to initiate
    a video upload — that's a coach-tier action. This pins the membership
    role check, which is a separate decision from the user role check."""
    await _register(storage_client, _coach_payload("roster-coach@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    # Player joins via invite code.
    await _register(storage_client, _player_payload("roster-player@example.com"))
    join = await storage_client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 42},
    )
    assert join.status_code == 200

    response = await storage_client.post(f"{API}/videos/upload", json=_upload_body(game["id"]))
    # Player-tier membership has no write access to video uploads.
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_player_member_cannot_patch_game(storage_client: AsyncClient) -> None:
    """A player on the team must not be able to PATCH the team's game score —
    scoreboard edits are coach-tier."""
    await _register(storage_client, _coach_payload("patch-coach@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    await _register(storage_client, _player_payload("patch-player@example.com"))
    join = await storage_client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 10},
    )
    assert join.status_code == 200

    response = await storage_client.patch(
        f"{API}/games/{game['id']}", json={"score_team": 77, "score_opponent": 42}
    )
    assert response.status_code == 403


# ---- CSRF guard across state-changing endpoints ---------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_video_upload_rejects_cookie_auth_without_csrf(
    storage_client: AsyncClient,
) -> None:
    """Cookie-authenticated POSTs to the upload endpoint must still carry a
    matching X-CSRF-Token — the double-submit guard must apply to
    non-auth mutations, not just /auth/*."""
    from httpx import AsyncClient as BareClient
    from nextballup_api.main import app

    await _register(storage_client, _coach_payload("csrf-upload@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    transport = ASGITransport(app=app)
    async with BareClient(transport=transport, base_url="http://test") as bare:
        for cookie in storage_client.cookies.jar:
            bare.cookies.set(cookie.name, cookie.value or "", cookie.domain, cookie.path)
        # No CSRF header → must fail with CSRF_FAILED even though we have valid
        # auth cookies.
        response = await bare.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"]),
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.CSRF_FAILED


@pytest.mark.asyncio(loop_scope="session")
async def test_cookie_auth_with_bearer_header_still_requires_csrf(
    storage_client: AsyncClient,
) -> None:
    """A junk Authorization header must not suppress CSRF when auth cookies
    are present. The dependency layer prefers cookies, so the middleware must
    classify cookie+Bearer as cookie-authenticated."""
    from httpx import AsyncClient as BareClient
    from nextballup_api.main import app

    await _register(storage_client, _coach_payload("csrf-bearer-upload@example.com"))
    team = (await storage_client.post(f"{API}/teams", json=_team_body())).json()
    game = (await storage_client.post(f"{API}/games", json=_game_body(team["id"]))).json()

    transport = ASGITransport(app=app)
    async with BareClient(transport=transport, base_url="http://test") as bare:
        for cookie in storage_client.cookies.jar:
            bare.cookies.set(cookie.name, cookie.value or "", cookie.domain, cookie.path)
        response = await bare.post(
            f"{API}/videos/upload",
            json=_upload_body(game["id"]),
            headers={"Authorization": "Bearer attacker-controlled-junk"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.CSRF_FAILED


@pytest.mark.asyncio(loop_scope="session")
async def test_account_delete_rejects_cookie_auth_without_csrf(
    storage_client: AsyncClient,
) -> None:
    """DELETE /auth/me is the single most dangerous authenticated action —
    a CSRF bypass there would let an attacker anonymize the victim's account.
    Pin that it still goes through the double-submit guard."""
    from httpx import AsyncClient as BareClient
    from nextballup_api.main import app

    await _register(storage_client, _coach_payload("csrf-delete-me@example.com"))

    transport = ASGITransport(app=app)
    async with BareClient(transport=transport, base_url="http://test") as bare:
        for cookie in storage_client.cookies.jar:
            bare.cookies.set(cookie.name, cookie.value or "", cookie.domain, cookie.path)
        response = await bare.delete(f"{API}/auth/me")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.CSRF_FAILED


# ---- Admin-endpoint misuse ------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_requeue_missing_video_returns_404_for_admin(
    storage_client: AsyncClient, db_session: Any
) -> None:
    """Admin can poke the requeue endpoint, but a non-existent video_id must
    still 404 — we don't want the admin surface to leak existence via
    differential responses."""
    # Import here to keep the test file's top-level imports narrow.
    from nextballup_api.security.jwt import create_access_token
    from nextballup_api.security.passwords import hash_password

    from nextballup_core.enums import UserRole
    from nextballup_core.settings import get_settings
    from nextballup_db.models.user import User

    admin = User(
        email="requeue-missing-admin@example.com",
        password_hash=hash_password("Password1!"),
        full_name="Admin",
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
    storage_client.cookies.clear()
    response = await storage_client.post(
        f"{API}/videos/{uuid.uuid4()}/processing/requeue",
        json={"stage": "transcode"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.VIDEO_NOT_FOUND
