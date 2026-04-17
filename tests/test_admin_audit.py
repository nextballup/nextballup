"""Admin-only audit log viewer.

These tests cover the authorization gate (non-admin rejected), the base
listing shape, the filter surface operators rely on for incident review,
and cursor pagination stability across the (created_at DESC, id DESC) sort
order.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from nextballup_api.security.jwt import create_access_token
from nextballup_api.security.passwords import hash_password
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction
from nextballup_core.enums import UserRole
from nextballup_core.settings import get_settings
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.user import User

API = "/api/v1"


async def _auth_headers(db_session: AsyncSession, *, role: UserRole, email: str) -> dict[str, str]:
    user = User(
        email=email,
        password_hash=hash_password("Password1!"),
        full_name=f"Test {role.value}",
        role=role,
    )
    db_session.add(user)
    await db_session.flush()
    token = create_access_token(
        subject=user.id,
        role=user.role,
        session_version=user.session_version,
        team_ids=[],
        settings=get_settings(),
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_audit_rows(db_session: AsyncSession, *, count: int, action: str) -> list[AuditLog]:
    rows: list[AuditLog] = []
    for i in range(count):
        row = AuditLog(
            action=action,
            actor_email=f"seed-{i}@example.com",
            extra={"seq": i},
        )
        db_session.add(row)
        rows.append(row)
    await db_session.flush()
    return rows


# ---- authorization --------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_coach_cannot_read_audit_log(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = await _auth_headers(db_session, role=UserRole.COACH, email="coach-audit@example.com")
    response = await client.get(f"{API}/admin/audit/logs", headers=headers)
    assert response.status_code == 403, response.text


@pytest.mark.asyncio(loop_scope="session")
async def test_player_cannot_read_audit_log(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = await _auth_headers(
        db_session, role=UserRole.PLAYER, email="player-audit@example.com"
    )
    response = await client.get(f"{API}/admin/audit/logs", headers=headers)
    assert response.status_code == 403, response.text


@pytest.mark.asyncio(loop_scope="session")
async def test_anonymous_cannot_read_audit_log(client: AsyncClient) -> None:
    response = await client.get(f"{API}/admin/audit/logs")
    assert response.status_code == 401, response.text


# ---- listing + filtering --------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_lists_recent_audit_rows(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_audit_rows(db_session, count=5, action="test.admin.list")
    headers = await _auth_headers(db_session, role=UserRole.ADMIN, email="admin-list@example.com")
    response = await client.get(
        f"{API}/admin/audit/logs",
        params={"action": "test.admin.list", "limit": 10},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 5
    seqs = {(item["extra"] or {}).get("seq") for item in body["items"]}
    # The sort is (created_at DESC, id DESC); in-test inserts share a clock
    # and id is a UUID, so we only assert completeness, not concrete order.
    assert seqs == {0, 1, 2, 3, 4}
    assert body["next_cursor"] is None
    audit_read = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.ADMIN_AUDIT_LOGS_VIEWED)
    )
    assert audit_read is not None
    assert audit_read.actor_email == "admin-list@example.com"
    assert audit_read.resource_type == "audit_log"
    assert (audit_read.extra or {}).get("result_count") == 5


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_filters_by_action(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_audit_rows(db_session, count=3, action="filter.match")
    await _seed_audit_rows(db_session, count=3, action="filter.other")
    headers = await _auth_headers(db_session, role=UserRole.ADMIN, email="admin-filter@example.com")
    response = await client.get(
        f"{API}/admin/audit/logs",
        params={"action": "filter.match"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3
    assert all(item["action"] == "filter.match" for item in body["items"])


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_pagination_cursor_roundtrip(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_audit_rows(db_session, count=7, action="pagination.roundtrip")
    headers = await _auth_headers(db_session, role=UserRole.ADMIN, email="admin-paging@example.com")
    first = await client.get(
        f"{API}/admin/audit/logs",
        params={"action": "pagination.roundtrip", "limit": 3},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    page1 = first.json()
    assert len(page1["items"]) == 3
    cursor = page1["next_cursor"]
    assert cursor, "first page must return a next_cursor when more rows exist"

    second = await client.get(
        f"{API}/admin/audit/logs",
        params={"action": "pagination.roundtrip", "limit": 3, "cursor": cursor},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    page2 = second.json()
    # Page boundary is strict — no row appears on both pages.
    page1_ids = {item["id"] for item in page1["items"]}
    page2_ids = {item["id"] for item in page2["items"]}
    assert not (page1_ids & page2_ids), "pagination must not repeat rows"
    assert len(page1_ids | page2_ids) == 6

    third = await client.get(
        f"{API}/admin/audit/logs",
        params={
            "action": "pagination.roundtrip",
            "limit": 3,
            "cursor": page2["next_cursor"],
        },
        headers=headers,
    )
    assert third.status_code == 200
    page3 = third.json()
    assert len(page3["items"]) == 1
    assert page3["next_cursor"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_invalid_cursor_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth_headers(
        db_session, role=UserRole.ADMIN, email="admin-bad-cursor@example.com"
    )
    response = await client.get(
        f"{API}/admin/audit/logs",
        params={"cursor": "not-a-real-cursor"},
        headers=headers,
    )
    assert response.status_code == 422, response.text
