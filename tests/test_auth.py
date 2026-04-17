from __future__ import annotations

import pytest
from fastapi import Response
from httpx import AsyncClient
from nextballup_api.security.cookies import clear_auth_cookies, set_auth_cookies
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.settings import Settings
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


def _set_cookie_headers(response: Response | AsyncClient | object) -> list[str]:
    if isinstance(response, Response):
        return [
            value.decode("latin-1") for name, value in response.raw_headers if name == b"set-cookie"
        ]
    headers = getattr(response, "headers", None)
    if headers is None:
        return []
    get_list = getattr(headers, "get_list", None)
    if callable(get_list):
        return list(get_list("set-cookie"))
    return []


def _non_deleted_cookie_headers(headers: list[str], *, name: str) -> list[str]:
    return [value for value in headers if value.startswith(f"{name}=") and "Max-Age=0" not in value]


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
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert "id" in body and "created_at" in body

    cookies = response.cookies
    assert "nbu_access_token" in cookies
    assert "nbu_refresh_token" in cookies
    refresh_headers = _non_deleted_cookie_headers(
        _set_cookie_headers(response), name="nbu_refresh_token"
    )
    assert refresh_headers
    assert "Path=/api/v1/auth/refresh" in refresh_headers[0]

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
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert body["user"]["email"] == payload["email"]
    assert body["user"]["role"] == "coach"
    assert response.cookies.get("nbu_access_token")
    assert response.cookies.get("nbu_refresh_token")

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
    # Non-browser API clients can still read the access cookie from the
    # Set-Cookie header to use it as a bearer; the JSON body no longer
    # carries the token.
    access_token = register.cookies.get("nbu_access_token")
    assert access_token

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
    original_refresh = register.cookies.get("nbu_refresh_token")
    assert original_refresh

    response = await client.post(f"{API}/auth/refresh", json={})
    assert response.status_code == 200
    body = response.json()
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert body["refreshed_at"]
    # Rotation still happens in cookies even though tokens never appear in
    # the body — the new refresh cookie must not equal the prior one.
    rotated_refresh = response.cookies.get("nbu_refresh_token")
    assert rotated_refresh
    assert rotated_refresh != original_refresh
    refresh_headers = _non_deleted_cookie_headers(
        _set_cookie_headers(response), name="nbu_refresh_token"
    )
    assert refresh_headers
    assert "Path=/api/v1/auth/refresh" in refresh_headers[0]

    actions = await _audit_actions(db_session, str(payload["email"]))
    assert AuditAction.USER_REFRESH_SUCCEEDED in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_rejects_body_refresh_token_field(client: AsyncClient) -> None:
    """Legacy `{refresh_token: "..."}` bodies must be rejected so a caller
    can't bypass the cookie-only contract by passing a stolen token in JSON."""
    payload = _register_payload("refresh-body@example.com")
    register = await client.post(f"{API}/auth/register", json=payload)
    assert register.status_code == 201

    response = await client.post(f"{API}/auth/refresh", json={"refresh_token": "some-token"})
    assert response.status_code == 422


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
    access_token = register.cookies.get("nbu_access_token")
    assert access_token

    response = await client.post(f"{API}/auth/logout")
    assert response.status_code == 204

    actions = await _audit_actions(db_session, str(payload["email"]))
    assert AuditAction.USER_LOGOUT in actions

    client.cookies.clear()
    me = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 401


def test_host_prefix_applies_only_to_root_scoped_access_cookie() -> None:
    settings = Settings(
        cookie_secure=True,
        cookie_samesite="strict",
        cookie_host_prefix=True,
        cookie_domain=None,
        jwt_private_key="test-private-key",
        jwt_public_key="test-public-key",
    )
    response = Response()
    set_auth_cookies(response, access_token="access", refresh_token="refresh", settings=settings)
    cookie_headers = _set_cookie_headers(response)

    access_headers = _non_deleted_cookie_headers(cookie_headers, name="__Host-nbu_access_token")
    refresh_headers = _non_deleted_cookie_headers(cookie_headers, name="nbu_refresh_token")
    host_refresh_headers = _non_deleted_cookie_headers(
        cookie_headers, name="__Host-nbu_refresh_token"
    )

    assert access_headers
    assert "Path=/" in access_headers[0]
    assert refresh_headers
    assert "Path=/api/v1/auth/refresh" in refresh_headers[0]
    assert not host_refresh_headers


def test_clear_auth_cookies_clears_refresh_cookie_on_both_paths() -> None:
    settings = Settings(
        cookie_secure=True,
        cookie_samesite="strict",
        cookie_host_prefix=True,
        cookie_domain=None,
        jwt_private_key="test-private-key",
        jwt_public_key="test-public-key",
    )
    response = Response()
    clear_auth_cookies(response, settings=settings)
    cookie_headers = _set_cookie_headers(response)

    assert any(
        header.startswith("nbu_refresh_token=") and "Path=/api/v1/auth/refresh" in header
        for header in cookie_headers
    )
    assert any(
        header.startswith("nbu_refresh_token=") and "Path=/" in header for header in cookie_headers
    )


# ---- CSRF ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_cookie_auth_mutation_without_csrf_header_is_rejected(
    client: AsyncClient,
) -> None:
    """A cookie-authenticated mutation must carry a matching X-CSRF-Token or
    it's rejected before it reaches the router — the core of the double-submit
    guard. We strip the event-hook mirror by sending a raw request via
    httpx.Client-level override: easiest path is to build a fresh client with
    no hook and inherited cookies."""
    from httpx import ASGITransport
    from httpx import AsyncClient as BareClient
    from nextballup_api.main import app

    register = await client.post(
        f"{API}/auth/register", json=_register_payload("csrf-a@example.com")
    )
    assert register.status_code == 201
    cookie_jar = client.cookies.jar

    transport = ASGITransport(app=app)
    async with BareClient(transport=transport, base_url="http://test") as bare:
        for cookie in cookie_jar:
            bare.cookies.set(cookie.name, cookie.value or "", cookie.domain, cookie.path)
        # No X-CSRF-Token header → must be rejected.
        response = await bare.post(f"{API}/auth/logout")
        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == ErrorCode.CSRF_FAILED


@pytest.mark.asyncio(loop_scope="session")
async def test_cookie_auth_mutation_with_mismatched_csrf_is_rejected(
    client: AsyncClient,
) -> None:
    """Cookie-auth path must reject when the header does not equal the cookie —
    this is what makes the double-submit guard actually work against an
    attacker who can set their own header but not read our cookie."""
    from httpx import ASGITransport
    from httpx import AsyncClient as BareClient
    from nextballup_api.main import app

    register = await client.post(
        f"{API}/auth/register", json=_register_payload("csrf-b@example.com")
    )
    assert register.status_code == 201

    transport = ASGITransport(app=app)
    async with BareClient(transport=transport, base_url="http://test") as bare:
        for cookie in client.cookies.jar:
            bare.cookies.set(cookie.name, cookie.value or "", cookie.domain, cookie.path)
        response = await bare.post(
            f"{API}/auth/logout",
            headers={"X-CSRF-Token": "this-is-not-the-cookie"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == ErrorCode.CSRF_FAILED


@pytest.mark.asyncio(loop_scope="session")
async def test_bearer_auth_mutation_bypasses_csrf(client: AsyncClient) -> None:
    """Bearer-authenticated mutations are CSRF-immune by construction: a
    browser cross-origin attacker can't set Authorization headers, and API
    clients already hold the token in hand. Cookies must be cleared so the
    middleware sees a Bearer-only request."""
    from httpx import ASGITransport
    from httpx import AsyncClient as BareClient
    from nextballup_api.main import app

    register = await client.post(
        f"{API}/auth/register", json=_register_payload("csrf-c@example.com")
    )
    access_token = register.cookies.get("nbu_access_token")
    assert access_token

    transport = ASGITransport(app=app)
    async with BareClient(transport=transport, base_url="http://test") as bare:
        # No cookies, no CSRF header — just Bearer.
        response = await bare.post(
            f"{API}/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 204
