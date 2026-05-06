from __future__ import annotations

import os
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from nextballup_api.main import app
from nextballup_api.routers.videos import get_storage
from nextballup_api.storage import PresignedPart, PresignedUpload
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import UploadMethod
from nextballup_core.settings import reload_settings
from nextballup_db.engine import dispose_engine, reset_engine_for_url
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.team import Team, TeamInvite, TeamMembership
from scripts.configure_runtime_db_role import _set_runtime_role_password
from tests.csrf_helper import make_csrf_mirror_hook

API = "/api/v1"


def _coach_payload(email: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "email": email,
        "password": "Password1!",
        "full_name": "Mike Johnson",
        "role": "coach",
    }
    payload.update(overrides)
    return payload


def _player_payload(email: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "email": email,
        "password": "Password1!",
        "full_name": "James Williams",
        "role": "player",
    }
    payload.update(overrides)
    return payload


def _team_create_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Lincoln Varsity Boys",
        "sport": "basketball",
        "level": "high_school",
        "institution": "Lincoln High School",
        "institution_type": "k12_school",
        "season": "2026-2027",
        "city": "Houston",
        "state": "TX",
        "conference": "District 18-6A",
    }
    body.update(overrides)
    return body


async def _register(client: AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


async def _login(client: AsyncClient, email: str, password: str) -> None:
    """Replace the client's cookies with a fresh session for the given user."""
    client.cookies.clear()
    response = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


async def _audit_actions_for_team(session: AsyncSession, team_id: str) -> list[str]:
    rows = await session.execute(
        select(AuditLog.action).where(AuditLog.team_id == team_id).order_by(AuditLog.created_at)
    )
    return [r[0] for r in rows.all()]


async def _force_team_rls(session: AsyncSession) -> None:
    for table in ("teams", "team_memberships", "team_invites", "audit_logs"):
        await session.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))


class _RuntimeUploadStorage:
    def __init__(self) -> None:
        self.object_sizes: dict[str, int] = {}
        self.completed_multiparts: list[dict[str, Any]] = []

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
        parts = tuple(
            PresignedPart(
                part_number=part_number,
                url=f"https://fake-storage.test/{key}?partNumber={part_number}",
            )
            for part_number in range(1, 4)
        )
        self.object_sizes[key] = file_size_bytes
        return PresignedUpload(
            method=UploadMethod.MULTIPART,
            upload_id="runtime-upload-test",
            parts=parts,
            part_size_bytes=100 * 1024 * 1024,
        )

    def complete_multipart(self, *, key: str, upload_id: str, parts: list[dict[str, Any]]) -> None:
        self.completed_multiparts.append({"key": key, "upload_id": upload_id, "parts": parts})

    def abort_multipart(self, *, key: str, upload_id: str) -> None:
        self.object_sizes.pop(key, None)
        _ = upload_id

    def head_object(self, *, key: str) -> dict[str, Any] | None:
        size = self.object_sizes.get(key)
        if size is None:
            return None
        return {"ContentLength": size}


# ---- POST /teams -----------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_coach_can_create_team_and_becomes_head_coach(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _register(client, _coach_payload("create-team@example.com"))

    response = await client.post(f"{API}/teams", json=_team_create_body())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Lincoln Varsity Boys"
    assert body["sport"] == "basketball"
    assert body["member_count"] == 1
    assert len(body["invite_code"]) >= 8
    team_id = body["id"]

    membership = await db_session.scalar(
        select(TeamMembership).where(TeamMembership.team_id == team_id)
    )
    assert membership is not None
    assert membership.team_role.value == "head_coach"

    actions = await _audit_actions_for_team(db_session, team_id)
    assert AuditAction.TEAM_CREATED in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_runtime_role_can_create_team_through_api_without_owner_override(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "RuntimeRoleApiTeamTest1"
    owner_url = os.environ["DATABASE_URL"]
    runtime_url = owner_url.replace(
        "nextballup:nextballup_dev@",
        f"nextballup_app:{password}@",
    )
    async with engine.begin() as connection:
        await _set_runtime_role_password(connection, "nextballup_app", password)

    user_id: str | None = None
    team_id: str | None = None
    monkeypatch.setenv("DATABASE_URL_RUNTIME", runtime_url)
    reload_settings()
    await dispose_engine()
    reset_engine_for_url(runtime_url)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            event_hooks={"request": [make_csrf_mirror_hook()]},
        ) as runtime_client:
            register = await runtime_client.post(
                f"{API}/auth/register",
                json=_coach_payload("runtime-create-team@example.com"),
            )
            assert register.status_code == 201, register.text
            user_id = register.json()["id"]

            response = await runtime_client.post(
                f"{API}/teams",
                json=_team_create_body(name="Runtime Alpha Team", level="aau_club"),
            )
            assert response.status_code == 201, response.text
            body = response.json()
            team_id = body["id"]
            assert body["name"] == "Runtime Alpha Team"
            assert body["member_count"] == 1
    finally:
        monkeypatch.delenv("DATABASE_URL_RUNTIME", raising=False)
        reload_settings()
        await dispose_engine()
        reset_engine_for_url(owner_url)
        app.dependency_overrides.clear()
        _ = (team_id, user_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_runtime_role_can_initiate_video_upload_through_api(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "RuntimeRoleApiUploadTest1"
    owner_url = os.environ["DATABASE_URL"]
    runtime_url = owner_url.replace(
        "nextballup:nextballup_dev@",
        f"nextballup_app:{password}@",
    )
    async with engine.begin() as connection:
        await _set_runtime_role_password(connection, "nextballup_app", password)

    monkeypatch.setenv("DATABASE_URL_RUNTIME", runtime_url)
    reload_settings()
    await dispose_engine()
    reset_engine_for_url(runtime_url)
    storage = _RuntimeUploadStorage()
    try:
        app.dependency_overrides[get_storage] = lambda: storage
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            event_hooks={"request": [make_csrf_mirror_hook()]},
        ) as runtime_client:
            register = await runtime_client.post(
                f"{API}/auth/register",
                json=_coach_payload("runtime-upload@example.com"),
            )
            assert register.status_code == 201, register.text

            team = await runtime_client.post(
                f"{API}/teams",
                json=_team_create_body(name="Runtime Upload Team", level="aau_club"),
            )
            assert team.status_code == 201, team.text
            team_id = team.json()["id"]

            game = await runtime_client.post(
                f"{API}/games",
                json={
                    "team_id": team_id,
                    "opponent_name": "Runtime Opponent",
                    "game_type": "scrimmage",
                    "date": "2026-05-06",
                    "is_home": True,
                },
            )
            assert game.status_code == 201, game.text

            upload = await runtime_client.post(
                f"{API}/videos/upload",
                json={
                    "game_id": game.json()["id"],
                    "filename": "runtime-upload.mov",
                    "content_type": "video/quicktime",
                    "file_size_bytes": 2 * 1024 * 1024 * 1024,
                    "camera_position": "sideline",
                    "camera_height": "elevated",
                },
            )
            assert upload.status_code == 201, upload.text
            body = upload.json()
            assert body["upload_method"] == UploadMethod.MULTIPART
            assert body["upload_id"] == "runtime-upload-test"
            assert len(body["part_urls"]) == 3

            complete = await runtime_client.post(
                f"{API}/videos/{body['id']}/complete",
                json={
                    "parts": [
                        {"part_number": part["part_number"], "etag": f"etag-{part['part_number']}"}
                        for part in body["part_urls"]
                    ]
                },
            )
            assert complete.status_code == 200, complete.text
            assert storage.completed_multiparts[-1]["upload_id"] == body["upload_id"]
    finally:
        monkeypatch.delenv("DATABASE_URL_RUNTIME", raising=False)
        reload_settings()
        await dispose_engine()
        reset_engine_for_url(owner_url)
        app.dependency_overrides.clear()


@pytest.mark.asyncio(loop_scope="session")
async def test_player_cannot_create_team(client: AsyncClient) -> None:
    await _register(client, _player_payload("player-cant-create@example.com"))
    response = await client.post(f"{API}/teams", json=_team_create_body())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN


@pytest.mark.asyncio(loop_scope="session")
async def test_create_team_requires_authentication(client: AsyncClient) -> None:
    client.cookies.clear()
    response = await client.post(f"{API}/teams", json=_team_create_body())
    assert response.status_code == 401


@pytest.mark.asyncio(loop_scope="session")
async def test_create_team_validates_payload(client: AsyncClient) -> None:
    await _register(client, _coach_payload("validate-team@example.com"))
    bad = _team_create_body(level="not-a-real-level")
    response = await client.post(f"{API}/teams", json=bad)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED


# ---- POST /teams/{id}/invite ----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_coach_can_create_invite(client: AsyncClient, db_session: AsyncSession) -> None:
    await _register(client, _coach_payload("invite-coach@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    response = await client.post(
        f"{API}/teams/{team['id']}/invite",
        json={"role": "player", "max_uses": 5, "expires_in_days": 14},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["remaining_uses"] == 5
    assert body["role"] == "player"
    assert len(body["invite_code"]) >= 8
    assert body["invite_url"].endswith(f"/join/{body['invite_code']}")

    actions = await _audit_actions_for_team(db_session, team["id"])
    assert AuditAction.TEAM_INVITE_CREATED in actions

    audit_entry = await db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == AuditAction.TEAM_INVITE_CREATED,
            AuditLog.team_id == team["id"],
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert audit_entry is not None
    assert audit_entry.resource_id is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_non_coach_member_cannot_create_invite(client: AsyncClient) -> None:
    coach = _coach_payload("invite-owner@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    player = _player_payload("invite-player@example.com")
    await _register(client, player)
    join = await client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 21},
    )
    assert join.status_code == 200, join.text

    response = await client.post(
        f"{API}/teams/{team['id']}/invite",
        json={"role": "player", "max_uses": 5, "expires_in_days": 14},
    )
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_non_member_cannot_create_invite(client: AsyncClient) -> None:
    coach_a = _coach_payload("coach-a@example.com")
    await _register(client, coach_a)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    coach_b = _coach_payload("coach-b@example.com")
    await _register(client, coach_b)
    response = await client.post(
        f"{API}/teams/{team['id']}/invite",
        json={"role": "player", "max_uses": 5, "expires_in_days": 14},
    )
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_assistant_coach_cannot_issue_head_coach_invite(client: AsyncClient) -> None:
    await _register(client, _coach_payload("headcoach-owner@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()
    assistant_invite = (
        await client.post(
            f"{API}/teams/{team['id']}/invite",
            json={"role": "assistant_coach", "max_uses": 1, "expires_in_days": 14},
        )
    ).json()

    await _register(client, _coach_payload("assistant-joiner@example.com"))
    join = await client.post(
        f"{API}/teams/join", json={"invite_code": assistant_invite["invite_code"]}
    )
    assert join.status_code == 200, join.text

    response = await client.post(
        f"{API}/teams/{team['id']}/invite",
        json={"role": "head_coach", "max_uses": 1, "expires_in_days": 14},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN


# ---- POST /teams/join -----------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_player_joins_team_via_default_invite_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    coach = _coach_payload("join-coach@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    player = _player_payload("join-player@example.com")
    await _register(client, player)
    response = await client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"].lower(), "jersey_number": 23},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == team["id"]
    assert body["invite_code"] is None
    assert body["membership"]["jersey_number"] == 23
    assert body["membership"]["team_role"] == "player"

    actions = await _audit_actions_for_team(db_session, team["id"])
    assert AuditAction.TEAM_JOIN_SUCCEEDED in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_player_join_requires_jersey_number(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _register(client, _coach_payload("nojer-coach@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    await _register(client, _player_payload("nojer-player@example.com"))
    response = await client.post(f"{API}/teams/join", json={"invite_code": team["invite_code"]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.JERSEY_NUMBER_REQUIRED

    failed = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == AuditAction.TEAM_JOIN_FAILED)
    )
    assert failed is not None and failed >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_join_rejects_unknown_invite_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _register(client, _player_payload("unknown@example.com"))
    response = await client.post(
        f"{API}/teams/join", json={"invite_code": "NOTAREALCODE", "jersey_number": 10}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.INVITE_NOT_FOUND


@pytest.mark.asyncio(loop_scope="session")
async def test_join_rejects_expired_team_invite(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    coach = _coach_payload("expire-coach@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()
    invite = (
        await client.post(
            f"{API}/teams/{team['id']}/invite",
            json={"role": "player", "max_uses": 5, "expires_in_days": 1},
        )
    ).json()

    # Force the invite to expire by rewriting expires_at.
    await db_session.execute(
        text(
            "UPDATE team_invites SET expires_at = now() - interval '1 hour' WHERE invite_code = :c"
        ).bindparams(c=invite["invite_code"])
    )
    await db_session.commit()

    await _register(client, _player_payload("expire-player@example.com"))
    response = await client.post(
        f"{API}/teams/join",
        json={"invite_code": invite["invite_code"], "jersey_number": 22},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.INVITE_EXPIRED


@pytest.mark.asyncio(loop_scope="session")
async def test_join_rejects_exhausted_invite(client: AsyncClient, db_session: AsyncSession) -> None:
    coach = _coach_payload("exhaust-coach@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()
    invite = (
        await client.post(
            f"{API}/teams/{team['id']}/invite",
            json={"role": "player", "max_uses": 1, "expires_in_days": 30},
        )
    ).json()

    await _register(client, _player_payload("first-joiner@example.com"))
    first = await client.post(
        f"{API}/teams/join",
        json={"invite_code": invite["invite_code"], "jersey_number": 5},
    )
    assert first.status_code == 200

    await _register(client, _player_payload("second-joiner@example.com"))
    second = await client.post(
        f"{API}/teams/join",
        json={"invite_code": invite["invite_code"], "jersey_number": 6},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == ErrorCode.INVITE_EXHAUSTED


@pytest.mark.asyncio(loop_scope="session")
async def test_join_rejects_duplicate_membership(client: AsyncClient) -> None:
    await _register(client, _coach_payload("dup-coach@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()
    await _register(client, _player_payload("dup-player@example.com"))
    first = await client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 99},
    )
    assert first.status_code == 200, first.text

    response = await client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 99},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.ALREADY_MEMBER


@pytest.mark.asyncio(loop_scope="session")
async def test_join_rejects_jersey_collision(client: AsyncClient) -> None:
    coach = _coach_payload("jersey-coach@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    await _register(client, _player_payload("jersey-a@example.com"))
    first = await client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 23},
    )
    assert first.status_code == 200

    await _register(client, _player_payload("jersey-b@example.com"))
    response = await client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 23},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.JERSEY_NUMBER_TAKEN


@pytest.mark.asyncio(loop_scope="session")
async def test_player_cannot_join_with_coach_tier_invite(client: AsyncClient) -> None:
    await _register(client, _coach_payload("coach-tier-owner@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()
    invite = (
        await client.post(
            f"{API}/teams/{team['id']}/invite",
            json={"role": "assistant_coach", "max_uses": 1, "expires_in_days": 30},
        )
    ).json()

    await _register(client, _player_payload("coach-tier-player@example.com"))
    response = await client.post(
        f"{API}/teams/join",
        json={"invite_code": invite["invite_code"], "jersey_number": 12},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.INVITE_ROLE_MISMATCH


# ---- GET /teams/{id} and /members -----------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_member_can_read_team_detail(client: AsyncClient) -> None:
    await _register(client, _coach_payload("read-coach@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    response = await client.get(f"{API}/teams/{team['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == team["id"]
    assert body["invite_code"] == team["invite_code"]
    assert body["my_team_role"] == "head_coach"
    assert body["member_count"] == 1
    assert len(body["members"]) == 1
    assert body["members"][0]["team_role"] == "head_coach"


@pytest.mark.asyncio(loop_scope="session")
async def test_non_member_cannot_read_team_detail(client: AsyncClient) -> None:
    await _register(client, _coach_payload("owner-detail@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    await _register(client, _coach_payload("snoop-detail@example.com"))
    response = await client.get(f"{API}/teams/{team['id']}")
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_player_member_can_read_team_detail_without_invite_code(client: AsyncClient) -> None:
    coach = _coach_payload("detail-owner@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    player = _player_payload("detail-player@example.com")
    await _register(client, player)
    join = await client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 14},
    )
    assert join.status_code == 200, join.text

    response = await client.get(f"{API}/teams/{team['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == team["id"]
    assert body["my_team_role"] == "player"
    assert body["invite_code"] is None
    assert body["member_count"] == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_member_can_list_members(client: AsyncClient) -> None:
    coach = _coach_payload("members-coach@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    await _register(client, _player_payload("members-player@example.com"))
    join = await client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 7},
    )
    assert join.status_code == 200

    await _login(client, coach["email"], coach["password"])
    response = await client.get(f"{API}/teams/{team['id']}/members")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    roles = {m["team_role"] for m in body["members"]}
    assert roles == {"head_coach", "player"}


@pytest.mark.asyncio(loop_scope="session")
async def test_non_member_cannot_list_members(client: AsyncClient) -> None:
    await _register(client, _coach_payload("members-owner@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    await _register(client, _coach_payload("members-snoop@example.com"))
    response = await client.get(f"{API}/teams/{team['id']}/members")
    assert response.status_code == 403


# ---- Tenant-context wiring ------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_team_scoped_request_sets_tenant_context_guc(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """create_team must SET LOCAL app.current_team_id so RLS policies bind to
    the freshly-created team. We assert the GUC by inspecting the same session
    that processed the request — when the test session is shared with the
    request handler (via dependency_overrides), the SET LOCAL persists until
    the outer transaction is rolled back on test teardown."""
    await _register(client, _coach_payload("guc@example.com"))
    create_response = await client.post(f"{API}/teams", json=_team_create_body())
    assert create_response.status_code == 201
    team_id = create_response.json()["id"]

    guc_value = await db_session.scalar(text("SELECT current_setting('app.current_team_id', true)"))
    assert guc_value == team_id


@pytest.mark.asyncio(loop_scope="session")
async def test_team_creation_and_invites_work_under_force_rls(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _register(client, _coach_payload("force-rls-create@example.com"))
    await _force_team_rls(db_session)

    create = await client.post(f"{API}/teams", json=_team_create_body(name="Force RLS Team"))
    assert create.status_code == 201, create.text
    team = create.json()

    invite = await client.post(
        f"{API}/teams/{team['id']}/invite",
        json={"role": "player", "max_uses": 2, "expires_in_days": 14},
    )
    assert invite.status_code == 201, invite.text

    detail = await client.get(f"{API}/teams/{team['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == team["id"]

    invite_row = await db_session.scalar(
        select(TeamInvite).where(TeamInvite.invite_code == invite.json()["invite_code"])
    )
    assert invite_row is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_auth_me_loads_teams_under_force_rls(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    coach = _coach_payload("force-rls-auth@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body(name="Force RLS Auth"))).json()
    await _force_team_rls(db_session)

    response = await client.get(f"{API}/auth/me")
    assert response.status_code == 200, response.text
    team_ids = {t["id"] for t in response.json()["teams"]}
    assert team["id"] in team_ids


# ---- Tenant isolation (app-layer) -----------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_two_coaches_cannot_access_each_others_teams(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    coach_a = _coach_payload("isolation-a@example.com")
    await _register(client, coach_a)
    team_a = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    coach_b = _coach_payload("isolation-b@example.com")
    await _register(client, coach_b)
    team_b = (await client.post(f"{API}/teams", json=_team_create_body(name="Other Team"))).json()

    # Coach B reading Team A
    forbidden = await client.get(f"{API}/teams/{team_a['id']}")
    assert forbidden.status_code == 403

    # Coach B reading their own team is fine
    own = await client.get(f"{API}/teams/{team_b['id']}")
    assert own.status_code == 200
    assert own.json()["id"] == team_b["id"]


# ---- Independent client to verify no fixture cross-contamination ----------


# ---- GET /teams -----------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_list_my_teams_returns_teams_with_role_and_counts(
    client: AsyncClient,
) -> None:
    coach = _coach_payload("list-coach@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    await _register(client, _player_payload("list-player@example.com"))
    join = await client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 7},
    )
    assert join.status_code == 200, join.text

    await _login(client, coach["email"], coach["password"])
    response = await client.get(f"{API}/teams")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["teams"]) == 1
    entry = body["teams"][0]
    assert entry["id"] == team["id"]
    assert entry["my_team_role"] == "head_coach"
    assert entry["member_count"] == 2
    assert entry["game_count"] == 0
    # Coaches see the team's invite code.
    assert entry["invite_code"] == team["invite_code"]


@pytest.mark.asyncio(loop_scope="session")
async def test_list_my_teams_hides_invite_code_from_players(client: AsyncClient) -> None:
    coach = _coach_payload("hide-coach@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    player = _player_payload("hide-player@example.com")
    await _register(client, player)
    join = await client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 11},
    )
    assert join.status_code == 200

    # Player now lists their teams — should not see the invite_code.
    await _login(client, player["email"], player["password"])
    response = await client.get(f"{API}/teams")
    assert response.status_code == 200
    entry = response.json()["teams"][0]
    assert entry["my_team_role"] == "player"
    assert entry["invite_code"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_list_my_teams_returns_empty_for_team_less_user(client: AsyncClient) -> None:
    await _register(client, _player_payload("solo-player@example.com"))
    response = await client.get(f"{API}/teams")
    assert response.status_code == 200
    assert response.json() == {"teams": []}


@pytest.mark.asyncio(loop_scope="session")
async def test_list_my_teams_requires_authentication(client: AsyncClient) -> None:
    client.cookies.clear()
    response = await client.get(f"{API}/teams")
    assert response.status_code == 401


@pytest.mark.asyncio(loop_scope="session")
async def test_list_my_teams_does_not_leak_other_teams(client: AsyncClient) -> None:
    coach_a = _coach_payload("leaky-coach-a@example.com")
    await _register(client, coach_a)
    await client.post(f"{API}/teams", json=_team_create_body(name="Team A"))

    coach_b = _coach_payload("leaky-coach-b@example.com")
    await _register(client, coach_b)
    response = await client.get(f"{API}/teams")
    # Coach B just registered and hasn't created/joined a team yet.
    assert response.status_code == 200
    assert response.json() == {"teams": []}


@pytest.mark.asyncio(loop_scope="session")
async def test_soft_delete_team_hides_data_and_preserves_admin_audit_context(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    coach = _coach_payload("soft-delete-coach@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    deleted = await client.delete(f"{API}/teams/{team['id']}")
    assert deleted.status_code == 204, deleted.text

    list_response = await client.get(f"{API}/teams")
    assert list_response.status_code == 200, list_response.text
    assert list_response.json() == {"teams": []}

    detail = await client.get(f"{API}/teams/{team['id']}")
    assert detail.status_code == 404

    await db_session.execute(text("SELECT set_config('app.current_user_role', 'admin', true)"))
    await db_session.execute(text("SELECT set_config('app.include_deleted', 'true', true)"))
    row = await db_session.scalar(select(Team).where(Team.id == team["id"]))
    assert row is not None
    assert row.deleted_at is not None
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.team_id == team["id"],
            AuditLog.action == AuditAction.TEAM_DELETED,
        )
    )
    assert audit is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_soft_delete_team_is_idempotent(client: AsyncClient) -> None:
    await _register(client, _coach_payload("soft-delete-idempotent@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    first = await client.delete(f"{API}/teams/{team['id']}")
    assert first.status_code == 204, first.text
    second = await client.delete(f"{API}/teams/{team['id']}")
    assert second.status_code == 204, second.text


@pytest.mark.asyncio(loop_scope="session")
async def test_player_cannot_soft_delete_team(client: AsyncClient) -> None:
    coach = _coach_payload("soft-delete-deny-coach@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    player = _player_payload("soft-delete-deny-player@example.com")
    await _register(client, player)
    joined = await client.post(
        f"{API}/teams/join",
        json={"invite_code": team["invite_code"], "jersey_number": 9},
    )
    assert joined.status_code == 200, joined.text

    denied = await client.delete(f"{API}/teams/{team['id']}")
    assert denied.status_code == 403, denied.text

    await _login(client, coach["email"], coach["password"])
    detail = await client.get(f"{API}/teams/{team['id']}")
    assert detail.status_code == 200, detail.text


@pytest.mark.asyncio(loop_scope="session")
async def test_deleted_team_blocks_direct_db_writes_under_rls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _register(client, _coach_payload("deleted-team-rls-write@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()
    deleted = await client.delete(f"{API}/teams/{team['id']}")
    assert deleted.status_code == 204, deleted.text

    await db_session.execute(
        text("SELECT set_config('app.current_team_id', :team_id, true)"),
        {"team_id": team["id"]},
    )
    with pytest.raises(DBAPIError):
        await db_session.execute(
            text(
                """
                INSERT INTO games (
                    team_id, opponent_name, game_type, date, status,
                    periods, period_length_minutes
                )
                VALUES (
                    :id, 'RLS blocked', 'regular_season', '2026-11-01',
                    'scheduled', 4, 8
                )
                """
            ),
            {"id": team["id"]},
        )
    await db_session.rollback()


@pytest.mark.asyncio(loop_scope="session")
async def test_list_my_teams_works_under_force_rls(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    coach = _coach_payload("list-force-coach@example.com")
    await _register(client, coach)
    team = (await client.post(f"{API}/teams", json=_team_create_body(name="Force RLS List"))).json()
    await _force_team_rls(db_session)

    await _login(client, coach["email"], coach["password"])
    response = await client.get(f"{API}/teams")
    assert response.status_code == 200
    teams = response.json()["teams"]
    assert len(teams) == 1
    assert teams[0]["id"] == team["id"]
    assert teams[0]["member_count"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_audit_logs_reference_team_id(client: AsyncClient, db_session: AsyncSession) -> None:
    """Membership-creating actions must populate audit_logs.team_id so the
    tenant_isolation policy applies them under the right team context."""
    await _register(client, _coach_payload("auditteam@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_create_body())).json()

    rows = await db_session.execute(
        select(AuditLog.action, AuditLog.team_id).where(AuditLog.action == AuditAction.TEAM_CREATED)
    )
    matching = [r for r in rows.all() if str(r.team_id) == team["id"]]
    assert matching, "TEAM_CREATED audit row must reference team_id"
