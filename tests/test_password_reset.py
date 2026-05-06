from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import Request
from httpx import AsyncClient
from nextballup_api.email_delivery import (
    EmailMessage,
    register_email_provider,
)
from nextballup_api.password_reset import issue_password_reset_token
from nextballup_api.routers import auth as auth_router
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.errors import TooManyRequestsError
from nextballup_core.settings import get_settings, reload_settings
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.auth import RefreshSession
from nextballup_db.models.password_reset import PasswordResetToken
from nextballup_db.models.user import User

API = "/api/v1"


def _register_payload(email: str = "reset@example.com") -> dict[str, object]:
    return {
        "email": email,
        "password": "Password1!",
        "full_name": "Reset Coach",
        "role": "coach",
    }


def _token_from_log_line(line: str) -> str:
    payload = json.loads(line)
    assert isinstance(payload, dict)
    link_url = payload.get("link_url")
    assert isinstance(link_url, str)
    query = parse_qs(urlparse(link_url).query)
    tokens = query.get("token")
    assert tokens
    return tokens[0]


async def _audits(session: AsyncSession, email: str) -> list[str]:
    result = await session.execute(
        select(AuditLog.action)
        .where(AuditLog.actor_email == email.lower())
        .order_by(AuditLog.created_at)
    )
    return [row[0] for row in result.all()]


class _FailingProvider:
    name = "failing_password_reset"

    def send(self, message: EmailMessage) -> None:
        raise RuntimeError("provider unavailable")


class _FakeRequest:
    def __init__(self) -> None:
        self.headers = {"user-agent": "pytest"}
        self.client = type("C", (), {"host": "127.0.0.1"})()


def _fake_request() -> Request:
    return cast("Request", _FakeRequest())


async def _set_runtime_role(session: AsyncSession) -> None:
    await session.execute(text("SET LOCAL ROLE nextballup_app"))


@pytest.fixture(autouse=True)
def _reset_email_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    yield
    monkeypatch.delenv("EMAIL_DELIVERY_PROVIDER", raising=False)
    monkeypatch.delenv("EMAIL_DELIVERY_LOG_PATH", raising=False)
    reload_settings()


@pytest.mark.asyncio(loop_scope="session")
async def test_password_reset_request_and_confirm_rotates_credentials(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "reset-email.jsonl"
    monkeypatch.setenv("EMAIL_DELIVERY_PROVIDER", "logging")
    monkeypatch.setenv("EMAIL_DELIVERY_LOG_PATH", str(log_path))
    reload_settings()

    register = await client.post(f"{API}/auth/register", json=_register_payload())
    assert register.status_code == 201, register.text
    old_access = register.cookies.get("nbu_access_token")
    assert old_access

    request = await client.post(
        f"{API}/auth/password/forgot",
        json={"email": "reset@example.com"},
    )
    assert request.status_code == 202, request.text
    assert request.json()["delivery"] == "logging"

    export = await client.get(f"{API}/auth/me/export")
    assert export.status_code == 200, export.text
    reset_exports = export.json()["password_reset_tokens"]
    assert len(reset_exports) == 1
    assert "token_hash" not in reset_exports[0]

    raw_token = _token_from_log_line(log_path.read_text(encoding="utf-8").strip())
    reset = await client.post(
        f"{API}/auth/password/reset",
        json={"token": raw_token, "new_password": "NewPassword1!"},
    )
    assert reset.status_code == 200, reset.text
    assert "reset_at" in reset.json()

    old_login = await client.post(
        f"{API}/auth/login",
        json={"email": "reset@example.com", "password": "Password1!"},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        f"{API}/auth/login",
        json={"email": "reset@example.com", "password": "NewPassword1!"},
    )
    assert new_login.status_code == 200, new_login.text

    client.cookies.clear()
    stale = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {old_access}"})
    assert stale.status_code == 401

    user = await db_session.scalar(select(User).where(User.email == "reset@example.com"))
    assert user is not None
    revoked = (
        await db_session.scalars(select(RefreshSession).where(RefreshSession.user_id == user.id))
    ).all()
    assert any(session.revoked_reason == "password_reset" for session in revoked)

    actions = await _audits(db_session, "reset@example.com")
    assert AuditAction.USER_PASSWORD_RESET_REQUESTED in actions
    assert AuditAction.USER_PASSWORD_RESET_SENT in actions
    assert AuditAction.USER_PASSWORD_RESET_SUCCEEDED in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_password_reset_request_does_not_enumerate_unknown_email(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "reset-email.jsonl"
    monkeypatch.setenv("EMAIL_DELIVERY_PROVIDER", "logging")
    monkeypatch.setenv("EMAIL_DELIVERY_LOG_PATH", str(log_path))
    reload_settings()

    response = await client.post(
        f"{API}/auth/password/forgot",
        json={"email": "missing-reset@example.com"},
    )
    assert response.status_code == 202, response.text
    assert response.json()["delivery"] == "logging"
    assert not log_path.exists()

    tokens = await db_session.scalar(select(PasswordResetToken))
    assert tokens is None
    actions = await _audits(db_session, "missing-reset@example.com")
    assert actions == [AuditAction.USER_PASSWORD_RESET_REQUESTED]


@pytest.mark.asyncio(loop_scope="session")
async def test_password_reset_tokens_are_rls_scoped_by_user_or_presented_hash(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await client.post(f"{API}/auth/register", json=_register_payload("rls-a@example.com"))
    await client.post(f"{API}/auth/register", json=_register_payload("rls-b@example.com"))
    user_a = await db_session.scalar(select(User).where(User.email == "rls-a@example.com"))
    user_b = await db_session.scalar(select(User).where(User.email == "rls-b@example.com"))
    assert user_a is not None
    assert user_b is not None
    issued_a = await issue_password_reset_token(
        db_session,
        user=user_a,
        request=_fake_request(),
        settings=get_settings(),
    )
    issued_b = await issue_password_reset_token(
        db_session,
        user=user_b,
        request=_fake_request(),
        settings=get_settings(),
    )
    await db_session.flush()

    try:
        await _set_runtime_role(db_session)
        await db_session.execute(
            text("SELECT set_config('app.current_user_id', :user_id, true)"),
            {"user_id": str(user_a.id)},
        )

        own_token = await db_session.scalar(
            select(PasswordResetToken.token_hash).where(
                PasswordResetToken.token_hash == issued_a.token_hash
            )
        )
        assert own_token == issued_a.token_hash
        other_token = await db_session.scalar(
            select(PasswordResetToken.token_hash).where(
                PasswordResetToken.token_hash == issued_b.token_hash
            )
        )
        assert other_token is None

        await db_session.execute(
            text("SELECT set_config('app.current_password_reset_token_hash', :token_hash, true)"),
            {"token_hash": issued_b.token_hash},
        )
        presented_token = await db_session.scalar(
            select(PasswordResetToken.token_hash).where(
                PasswordResetToken.token_hash == issued_b.token_hash
            )
        )
        assert presented_token == issued_b.token_hash
    finally:
        await db_session.execute(text("RESET ROLE"))


@pytest.mark.asyncio(loop_scope="session")
async def test_password_reset_delivery_failure_is_audited_but_not_enumerable(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_email_provider("failing_password_reset", lambda _s: _FailingProvider())
    monkeypatch.setenv("EMAIL_DELIVERY_PROVIDER", "failing_password_reset")
    reload_settings()
    await client.post(f"{API}/auth/register", json=_register_payload("fail-reset@example.com"))

    response = await client.post(
        f"{API}/auth/password/forgot",
        json={"email": "fail-reset@example.com"},
    )
    assert response.status_code == 202, response.text

    actions = await _audits(db_session, "fail-reset@example.com")
    assert AuditAction.USER_PASSWORD_RESET_REQUESTED in actions
    assert AuditAction.USER_PASSWORD_RESET_REJECTED in actions
    user = await db_session.scalar(select(User).where(User.email == "fail-reset@example.com"))
    assert user is not None
    tokens = (
        await db_session.scalars(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        )
    ).all()
    assert len(tokens) == 1
    assert tokens[0].used_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_password_reset_second_request_supersedes_prior_token(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "reset-email.jsonl"
    monkeypatch.setenv("EMAIL_DELIVERY_PROVIDER", "logging")
    monkeypatch.setenv("EMAIL_DELIVERY_LOG_PATH", str(log_path))
    reload_settings()
    await client.post(f"{API}/auth/register", json=_register_payload("supersede@example.com"))

    first = await client.post(
        f"{API}/auth/password/forgot",
        json={"email": "supersede@example.com"},
    )
    assert first.status_code == 202, first.text
    second = await client.post(
        f"{API}/auth/password/forgot",
        json={"email": "supersede@example.com"},
    )
    assert second.status_code == 202, second.text

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    first_token = _token_from_log_line(lines[0])
    second_token = _token_from_log_line(lines[1])
    assert first_token != second_token

    user = await db_session.scalar(select(User).where(User.email == "supersede@example.com"))
    assert user is not None
    tokens = (
        await db_session.scalars(
            select(PasswordResetToken)
            .where(PasswordResetToken.user_id == user.id)
            .order_by(PasswordResetToken.created_at)
        )
    ).all()
    assert len(tokens) == 2
    assert [token.used_at is None for token in tokens] == [False, True]

    superseded = await client.post(
        f"{API}/auth/password/reset",
        json={"token": first_token, "new_password": "NewPassword1!"},
    )
    assert superseded.status_code == 409
    assert superseded.json()["error"]["code"] == ErrorCode.PASSWORD_RESET_TOKEN_USED

    usable = await client.post(
        f"{API}/auth/password/reset",
        json={"token": second_token, "new_password": "NewPassword1!"},
    )
    assert usable.status_code == 200, usable.text


@pytest.mark.asyncio(loop_scope="session")
async def test_password_reset_confirm_is_rate_limited(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_enforce_rate_limit(**kwargs: object) -> None:
        calls.append(kwargs)
        raise TooManyRequestsError(
            "Too many reset attempts",
            details={"retry_after_seconds": 30},
        )

    monkeypatch.setattr(auth_router, "enforce_rate_limit", fake_enforce_rate_limit)

    response = await client.post(
        f"{API}/auth/password/reset",
        json={"token": "this-token-is-long-enough", "new_password": "NewPassword1!"},
    )
    assert response.status_code == 429
    assert calls
    assert calls[0]["scope"] == "password_reset_confirm"
    assert calls[0]["subject"] == "password_reset_confirm"


@pytest.mark.asyncio(loop_scope="session")
async def test_password_reset_rejects_invalid_expired_and_reused_tokens(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await client.post(f"{API}/auth/register", json=_register_payload("token-cases@example.com"))
    user = await db_session.scalar(select(User).where(User.email == "token-cases@example.com"))
    assert user is not None
    settings = get_settings()

    invalid = await client.post(
        f"{API}/auth/password/reset",
        json={"token": "this-token-does-not-exist", "new_password": "NewPassword1!"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == ErrorCode.PASSWORD_RESET_TOKEN_INVALID

    expired = await issue_password_reset_token(
        db_session,
        user=user,
        request=_fake_request(),
        settings=settings,
    )
    expired.record.expires_at = datetime.now(tz=UTC) - timedelta(minutes=1)
    await db_session.commit()
    expired_response = await client.post(
        f"{API}/auth/password/reset",
        json={"token": expired.raw_token, "new_password": "NewPassword1!"},
    )
    assert expired_response.status_code == 400
    assert expired_response.json()["error"]["code"] == ErrorCode.PASSWORD_RESET_TOKEN_EXPIRED

    usable = await issue_password_reset_token(
        db_session,
        user=user,
        request=_fake_request(),
        settings=settings,
    )
    await db_session.commit()
    first = await client.post(
        f"{API}/auth/password/reset",
        json={"token": usable.raw_token, "new_password": "AnotherPass1!"},
    )
    assert first.status_code == 200, first.text
    replay = await client.post(
        f"{API}/auth/password/reset",
        json={"token": usable.raw_token, "new_password": "YetAnother1!"},
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == ErrorCode.PASSWORD_RESET_TOKEN_USED


@pytest.mark.asyncio(loop_scope="session")
async def test_password_reset_rejects_weak_new_password(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await client.post(f"{API}/auth/register", json=_register_payload("weak-reset@example.com"))
    user = await db_session.scalar(select(User).where(User.email == "weak-reset@example.com"))
    assert user is not None
    issued = await issue_password_reset_token(
        db_session,
        user=user,
        request=_fake_request(),
        settings=get_settings(),
    )
    await db_session.commit()

    response = await client.post(
        f"{API}/auth/password/reset",
        json={"token": issued.raw_token, "new_password": "weakpass"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED
