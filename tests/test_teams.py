from __future__ import annotations

from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.team import TeamInvite, TeamMembership

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
