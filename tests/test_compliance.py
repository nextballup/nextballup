"""Self-serve compliance endpoints: data export and account deletion.

These endpoints are the application-level realization of GDPR Article 15
(right of access) and Article 17 (right to erasure). The tests pin the
contract:

* Export returns the user's own profile, memberships, audit events, and
  uploaded-video metadata — and *only* theirs, not tenant-wide data.
* Deletion anonymizes the user row, deactivates memberships, bumps
  session_version so every outstanding token fails, and leaves
  tenant-owned audit/video rows referentially intact.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.team import TeamMembership
from nextballup_db.models.user import User

API = "/api/v1"


def _coach_payload(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "password": "Password1!",
        "full_name": "Export Coach",
        "role": "coach",
        "phone": "555-1234",
        "institution": "Lincoln High",
    }


def _team_payload() -> dict[str, Any]:
    return {
        "name": "Lincoln Varsity",
        "sport": "basketball",
        "level": "high_school",
        "institution_type": "k12_school",
        "season": "2025-26",
    }


async def _register(client: AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


# ---- GET /auth/me/export --------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_export_returns_profile_memberships_and_audits(
    client: AsyncClient,
) -> None:
    await _register(client, _coach_payload("export-coach@example.com"))
    # Drive a couple of auditable actions so the export has content.
    team_response = await client.post(f"{API}/teams", json=_team_payload())
    assert team_response.status_code == 201, team_response.text

    response = await client.get(f"{API}/auth/me/export")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["user"]["email"] == "export-coach@example.com"
    assert body["user"]["full_name"] == "Export Coach"
    assert body["user"]["phone"] == "555-1234"
    assert body["user"]["institution"] == "Lincoln High"
    assert body["user"]["role"] == "coach"
    # Consent fields must surface so the user can see their own consent state.
    assert body["user"]["biometric_consent"] is False
    assert body["user"]["parental_consent_on_file"] is False

    assert len(body["team_memberships"]) == 1
    membership = body["team_memberships"][0]
    assert membership["team_name"] == "Lincoln Varsity"
    assert membership["team_role"] == "head_coach"
    assert membership["is_active"] is True

    audit_actions = {event["action"] for event in body["audit_events"]}
    assert AuditAction.USER_REGISTER_SUCCEEDED in audit_actions
    assert AuditAction.TEAM_CREATED in audit_actions


@pytest.mark.asyncio(loop_scope="session")
async def test_export_does_not_include_other_users_data(
    client: AsyncClient,
) -> None:
    """Two separate users create separate teams. User A's export must not
    leak User B's memberships or audit rows."""
    await _register(client, _coach_payload("alice@example.com"))
    await client.post(f"{API}/teams", json=_team_payload())
    # Log out Alice; register Bob on the same client (cookies rotate).
    await client.post(f"{API}/auth/logout")
    await _register(
        client,
        _coach_payload("bob@example.com") | {"full_name": "Bob Other"},
    )
    await client.post(
        f"{API}/teams",
        json=_team_payload() | {"name": "Bob's Team"},
    )

    response = await client.get(f"{API}/auth/me/export")
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "bob@example.com"
    assert len(body["team_memberships"]) == 1
    assert body["team_memberships"][0]["team_name"] == "Bob's Team"
    # Bob's audit events must not mention Alice's actions.
    for event in body["audit_events"]:
        assert event.get("extra") is None or "alice" not in str(event["extra"]).lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_export_rejects_unauthenticated(client: AsyncClient) -> None:
    response = await client.get(f"{API}/auth/me/export")
    assert response.status_code == 401


@pytest.mark.asyncio(loop_scope="session")
async def test_export_writes_audit_entry(client: AsyncClient, db_session: AsyncSession) -> None:
    await _register(client, _coach_payload("audited-export@example.com"))
    response = await client.get(f"{API}/auth/me/export")
    assert response.status_code == 200

    actions = (
        await db_session.execute(
            select(AuditLog.action).where(AuditLog.actor_email == "audited-export@example.com")
        )
    ).all()
    assert any(row[0] == AuditAction.USER_DATA_EXPORTED for row in actions)


# ---- DELETE /auth/me ------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_anonymizes_user_row_and_revokes_sessions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _register(client, _coach_payload("gone@example.com"))
    me_before = (await client.get(f"{API}/auth/me")).json()
    user_id = me_before["id"]

    response = await client.delete(f"{API}/auth/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == user_id
    assert "deleted_at" in body

    # Subsequent calls must 401 — session_version has moved past the token's.
    after = await client.get(f"{API}/auth/me")
    assert after.status_code == 401

    # The row still exists (for audit/video FK integrity) but is anonymized.
    user = await db_session.scalar(select(User).where(User.id == user_id))
    assert user is not None
    assert user.email == f"deleted+{user_id}@nextballup.invalid"
    assert user.full_name == "[deleted user]"
    assert user.phone is None
    assert user.institution is None
    assert user.is_active is False
    # Password hash must not match any bcrypt digest — the user can never
    # log back in. We verify shape, not exact value, so changing the
    # sentinel is a single-point edit.
    assert not user.password_hash.startswith("$2")


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_deactivates_team_memberships(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _register(client, _coach_payload("leaver@example.com"))
    team = (await client.post(f"{API}/teams", json=_team_payload())).json()

    response = await client.delete(f"{API}/auth/me")
    assert response.status_code == 200

    memberships = (
        (
            await db_session.execute(
                select(TeamMembership).where(TeamMembership.team_id == team["id"])
            )
        )
        .scalars()
        .all()
    )
    assert memberships, "team should still have the historical membership row"
    assert all(m.is_active is False for m in memberships)


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_emits_audit_entry(client: AsyncClient, db_session: AsyncSession) -> None:
    await _register(client, _coach_payload("audit-delete@example.com"))
    response = await client.delete(f"{API}/auth/me")
    assert response.status_code == 200

    actions = (
        await db_session.execute(
            select(AuditLog.action).where(AuditLog.actor_email == "audit-delete@example.com")
        )
    ).all()
    assert any(row[0] == AuditAction.USER_ACCOUNT_DELETED for row in actions)


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_prevents_future_login(client: AsyncClient) -> None:
    """After deletion, the original credentials must never work again —
    even if someone re-creates the same email later, the original account
    stays dead."""
    payload = _coach_payload("perma-gone@example.com")
    await _register(client, payload)
    delete_resp = await client.delete(f"{API}/auth/me")
    assert delete_resp.status_code == 200

    login = await client.post(
        f"{API}/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert login.status_code == 401


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_rejects_unauthenticated(client: AsyncClient) -> None:
    response = await client.delete(f"{API}/auth/me")
    assert response.status_code == 401
