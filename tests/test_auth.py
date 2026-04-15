from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.user import User

API = "/api/v1"


def _register_payload(email: str = "coach@example.com", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "email": email,
        "password": "Password1!",
        "full_name": "Mike Johnson",
        "role": "coach",
    }
    payload.update(overrides)
    return payload


async def _audit_actions(session: AsyncSession, email: str) -> list[str]:
    result = await session.execute(
        select(AuditLog.action)
        .where(AuditLog.actor_email == email.lower())
        .order_by(AuditLog.created_at)
    )
    return [row[0] for row in result.all()]


# ---- /auth/register --------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_register_creates_user_and_returns_tokens(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _register_payload()
    response = await client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["email"] == payload["email"]
    assert body["full_name"] == payload["full_name"]
    assert body["role"] == "coach"
    assert body["access_token"] and body["refresh_token"]
    assert "id" in body and "created_at" in body

    cookies = response.cookies
    assert "nbu_access_token" in cookies
    assert "nbu_refresh_token" in cookies

    user = await db_session.scalar(select(User).where(User.email == payload["email"]))
    assert user is not None
    assert user.password_hash and user.password_hash != payload["password"]

    actions = await _audit_actions(db_session, str(payload["email"]))
    assert AuditAction.USER_REGISTER_SUCCEEDED in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_register_rejects_duplicate_email(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _register_payload("dup@example.com")
    first = await client.post(f"{API}/auth/register", json=payload)
    assert first.status_code == 201

    duplicate = await client.post(f"{API}/auth/register", json=payload)
    assert duplicate.status_code == 409
    body = duplicate.json()
    assert body["error"]["code"] == ErrorCode.EMAIL_TAKEN

    actions = await _audit_actions(db_session, str(payload["email"]))
    assert actions.count(AuditAction.USER_REGISTER_FAILED) == 1
    assert actions.count(AuditAction.USER_REGISTER_SUCCEEDED) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_register_rejects_weak_password(client: AsyncClient) -> None:
    payload = _register_payload("weak@example.com", password="short")
    response = await client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == ErrorCode.VALIDATION_FAILED


@pytest.mark.asyncio(loop_scope="session")
async def test_register_rejects_passwords_over_bcrypt_limit(client: AsyncClient) -> None:
    payload = _register_payload("longpw@example.com", password=("A1" * 40) + "!")
    response = await client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED


@pytest.mark.asyncio(loop_scope="session")
async def test_register_rejects_admin_self_signup(client: AsyncClient) -> None:
    payload = _register_payload("admin@example.com", role="admin")
    response = await client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
async def test_register_rejects_invalid_email(client: AsyncClient) -> None:
    payload = _register_payload("not-an-email")
    response = await client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 422


# ---- /auth/login -----------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_login_succeeds_with_correct_credentials(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _register_payload("login@example.com")
    register = await client.post(f"{API}/auth/register", json=payload)
    assert register.status_code == 201

    response = await client.post(
        f"{API}/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["user"]["email"] == payload["email"]
    assert body["user"]["role"] == "coach"
    assert response.cookies.get("nbu_access_token")

    actions = await _audit_actions(db_session, str(payload["email"]))
    assert AuditAction.USER_LOGIN_SUCCEEDED in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_login_fails_with_wrong_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _register_payload("wrongpw@example.com")
    await client.post(f"{API}/auth/register", json=payload)

    response = await client.post(
        f"{API}/auth/login",
        json={"email": payload["email"], "password": "WrongPass1!"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS

    actions = await _audit_actions(db_session, str(payload["email"]))
    assert AuditAction.USER_LOGIN_FAILED in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_login_fails_for_unknown_email(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await client.post(
        f"{API}/auth/login",
        json={"email": "ghost@example.com", "password": "Password1!"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS

    failed = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == AuditAction.USER_LOGIN_FAILED,
            AuditLog.actor_email == "ghost@example.com",
        )
    )
    assert failed == 1


# ---- /auth/me --------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_me_returns_current_user_with_cookie_auth(client: AsyncClient) -> None:
    payload = _register_payload("me@example.com")
    register = await client.post(f"{API}/auth/register", json=payload)
    assert register.status_code == 201

    response = await client.get(f"{API}/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == payload["email"]
    assert body["role"] == "coach"
    assert body["teams"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_me_accepts_bearer_token(client: AsyncClient) -> None:
    payload = _register_payload("bearer@example.com")
    register = await client.post(f"{API}/auth/register", json=payload)
    assert register.status_code == 201
    access_token = register.json()["access_token"]

    # Drop cookies to confirm the bearer header path works on its own.
    client.cookies.clear()

    response = await client.get(
        f"{API}/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    assert response.json()["email"] == payload["email"]


@pytest.mark.asyncio(loop_scope="session")
async def test_me_rejects_unauthenticated_request(client: AsyncClient) -> None:
    response = await client.get(f"{API}/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


@pytest.mark.asyncio(loop_scope="session")
async def test_me_rejects_garbage_token(client: AsyncClient) -> None:
    response = await client.get(f"{API}/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401


# ---- /auth/refresh ---------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_rotates_tokens_from_cookie(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _register_payload("refresh@example.com")
    register = await client.post(f"{API}/auth/register", json=payload)
    assert register.status_code == 201
    original_refresh = register.json()["refresh_token"]

    response = await client.post(f"{API}/auth/refresh", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["refresh_token"] != original_refresh

    actions = await _audit_actions(db_session, str(payload["email"]))
    assert AuditAction.USER_REFRESH_SUCCEEDED in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_rejects_missing_token(client: AsyncClient, db_session: AsyncSession) -> None:
    client.cookies.clear()
    response = await client.post(f"{API}/auth/refresh", json={})
    assert response.status_code == 401

    failed = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == AuditAction.USER_REFRESH_FAILED)
    )
    assert failed is not None and failed >= 1


# ---- /auth/logout ----------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_logout_clears_cookies_and_audits(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _register_payload("logout@example.com")
    register = await client.post(f"{API}/auth/register", json=payload)
    access_token = register.json()["access_token"]

    response = await client.post(f"{API}/auth/logout")
    assert response.status_code == 204

    actions = await _audit_actions(db_session, str(payload["email"]))
    assert AuditAction.USER_LOGOUT in actions

    client.cookies.clear()
    me = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 401
