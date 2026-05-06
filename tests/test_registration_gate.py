"""Registration gate behavior across deployment channels.

Covers:
- public/root behavior (status endpoint reflects mode and does not leak codes)
- beta `invite_only` denies callers with no/invalid invite, allows valid one
- `allowlist` denies non-listed emails, allows listed ones
- `disabled` rejects every caller
- failure paths still emit USER_REGISTER_FAILED audit rows so unauthorized
  signups remain observable
- production startup refuses an open registration channel
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from nextballup_api.billing import CheckoutSession, register_billing_provider
from nextballup_api.routers import auth as auth_router
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.errors import TooManyRequestsError
from nextballup_core.settings import get_settings, reload_settings
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.user import User

API = "/api/v1"

# Every env key any test in this file mutates; the fixture below snapshots all
# of them and restores them on teardown so order-of-fixture-finalizer issues
# (which monkeypatch struggles with when paired with an lru_cached settings)
# can't leak production-shaped env into other suites.
_ENV_KEYS = (
    "APP_ENV",
    "REGISTRATION_MODE",
    "REGISTRATION_INVITE_CODES",
    "REGISTRATION_EMAIL_ALLOWLIST",
    "COOKIE_SECURE",
    "COOKIE_SAMESITE",
    "COOKIE_HOST_PREFIX",
    "COOKIE_DOMAIN",
    "REDIS_URL",
    "MFA_SECRET_KEY",
    "CSRF_SECRET",
    "EMAIL_DELIVERY_PROVIDER",
    "EMAIL_VERIFICATION_FROM_ADDRESS",
    "POSTMARK_SERVER_TOKEN",
    "BILLING_PROVIDER",
    "FRONTEND_APP_URL",
    "DATABASE_URL_RUNTIME",
    "CV_DEMO_PREVIEW_ENABLED",
)


def _payload(email: str = "beta@example.com", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "email": email,
        "password": "Password1!",
        "full_name": "Beta Coach",
        "role": "coach",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def restore_env() -> Iterator[None]:
    """Snapshot/restore every env var these tests touch + reload settings."""
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reload_settings()


def _set_mode(
    mode: str,
    *,
    codes: str = "",
    allowlist: str = "",
) -> None:
    os.environ["REGISTRATION_MODE"] = mode
    os.environ["REGISTRATION_INVITE_CODES"] = codes
    os.environ["REGISTRATION_EMAIL_ALLOWLIST"] = allowlist
    reload_settings()


@pytest_asyncio.fixture(loop_scope="session")
async def gated_client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """Like the shared `client` fixture but rebuilt per test so settings
    changes are picked up by the FastAPI app's startup-validation path."""
    from nextballup_api.deps import get_db
    from nextballup_api.main import app

    from tests.csrf_helper import make_csrf_mirror_hook

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
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


async def _audit_actions(session: AsyncSession, email: str) -> list[tuple[str, dict[str, object]]]:
    result = await session.execute(
        select(AuditLog.action, AuditLog.extra)
        .where(AuditLog.actor_email == email.lower())
        .order_by(AuditLog.created_at)
    )
    return [(row[0], row[1] or {}) for row in result.all()]


# ---- /auth/registration/status -------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_status_reports_open_mode_by_default(
    gated_client: AsyncClient,
    restore_env: None,
) -> None:
    _set_mode("open")
    response = await gated_client.get(f"{API}/auth/registration/status")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "mode": "open",
        "invite_code_required": False,
        "is_open_to_public": True,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_status_does_not_leak_invite_codes_or_allowlist(
    gated_client: AsyncClient,
    restore_env: None,
) -> None:
    _set_mode(
        "invite_only",
        codes="PILOT-CODE-1,PILOT-CODE-2",
        allowlist="vip@example.com",
    )
    response = await gated_client.get(f"{API}/auth/registration/status")
    body = response.json()
    raw = response.text
    assert body["mode"] == "invite_only"
    assert body["invite_code_required"] is True
    assert body["is_open_to_public"] is False
    assert "PILOT-CODE-1" not in raw
    assert "vip@example.com" not in raw


@pytest.mark.asyncio(loop_scope="session")
async def test_status_is_not_cacheable(
    gated_client: AsyncClient,
    restore_env: None,
) -> None:
    _set_mode("invite_only", codes="PILOT-CODE-AAAA")
    response = await gated_client.get(f"{API}/auth/registration/status")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio(loop_scope="session")
async def test_status_disabled_signals_to_frontend(
    gated_client: AsyncClient,
    restore_env: None,
) -> None:
    _set_mode("disabled")
    response = await gated_client.get(f"{API}/auth/registration/status")
    body = response.json()
    assert body == {
        "mode": "disabled",
        "invite_code_required": False,
        "is_open_to_public": False,
    }


# ---- invite_only ---------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_invite_only_rejects_missing_code(
    gated_client: AsyncClient,
    db_session: AsyncSession,
    restore_env: None,
) -> None:
    _set_mode("invite_only", codes="PILOT-CODE-AAAA")
    payload = _payload("missing@example.com")
    response = await gated_client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.REGISTRATION_INVITE_REQUIRED
    user = await db_session.scalar(select(User).where(User.email == payload["email"]))
    assert user is None
    actions = await _audit_actions(db_session, str(payload["email"]))
    assert (
        AuditAction.USER_REGISTER_FAILED,
        {"reason": ErrorCode.REGISTRATION_INVITE_REQUIRED},
    ) in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_invite_only_rejects_invalid_code(
    gated_client: AsyncClient,
    db_session: AsyncSession,
    restore_env: None,
) -> None:
    _set_mode("invite_only", codes="PILOT-CODE-AAAA")
    payload = _payload("wrong@example.com", invite_code="WRONG-CODE")
    response = await gated_client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.REGISTRATION_INVITE_INVALID
    user = await db_session.scalar(select(User).where(User.email == payload["email"]))
    assert user is None


@pytest.mark.asyncio(loop_scope="session")
async def test_registration_uses_ip_level_rate_limit_before_invite_check(
    gated_client: AsyncClient,
    restore_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_mode("invite_only", codes="PILOT-CODE-AAAA")
    calls: list[dict[str, object]] = []

    async def noop_auth_rate_limit(**_kwargs: object) -> None:
        return None

    async def fake_rate_limit(**kwargs: object) -> None:
        calls.append(kwargs)
        raise TooManyRequestsError(
            "Too many registration attempts",
            details={"retry_after_seconds": 60},
        )

    monkeypatch.setattr(auth_router, "enforce_auth_rate_limit", noop_auth_rate_limit)
    monkeypatch.setattr(auth_router, "enforce_rate_limit", fake_rate_limit)

    response = await gated_client.post(
        f"{API}/auth/register",
        json=_payload("rotate-email@example.com", invite_code="WRONG-CODE"),
    )

    assert response.status_code == 429
    assert calls
    assert calls[0]["scope"] == "auth_register_ip"
    assert calls[0]["subject"] == "registration"


@pytest.mark.asyncio(loop_scope="session")
async def test_invite_only_allows_valid_code(
    gated_client: AsyncClient,
    db_session: AsyncSession,
    restore_env: None,
) -> None:
    _set_mode("invite_only", codes="PILOT-CODE-AAAA,OTHER-CODE-BBBB")
    payload = _payload("valid@example.com", invite_code="PILOT-CODE-AAAA")
    response = await gated_client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 201, response.text
    user = await db_session.scalar(select(User).where(User.email == payload["email"]))
    assert user is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_invite_only_accepts_copy_pasted_code_whitespace(
    gated_client: AsyncClient,
    db_session: AsyncSession,
    restore_env: None,
) -> None:
    _set_mode("invite_only", codes="PILOT-CODE-AAAA")
    payload = _payload("trimmed-code@example.com", invite_code="  PILOT-CODE-AAAA\n")
    response = await gated_client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 201, response.text
    user = await db_session.scalar(select(User).where(User.email == payload["email"]))
    assert user is not None


# ---- allowlist -----------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_allowlist_rejects_non_listed_email(
    gated_client: AsyncClient,
    db_session: AsyncSession,
    restore_env: None,
) -> None:
    _set_mode("allowlist", allowlist="vip@example.com")
    payload = _payload("intruder@example.com")
    response = await gated_client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.REGISTRATION_NOT_ALLOWLISTED
    user = await db_session.scalar(select(User).where(User.email == payload["email"]))
    assert user is None
    actions = await _audit_actions(db_session, str(payload["email"]))
    assert (
        AuditAction.USER_REGISTER_FAILED,
        {"reason": ErrorCode.REGISTRATION_NOT_ALLOWLISTED},
    ) in actions


@pytest.mark.asyncio(loop_scope="session")
async def test_allowlist_allows_listed_email_case_insensitive(
    gated_client: AsyncClient,
    db_session: AsyncSession,
    restore_env: None,
) -> None:
    _set_mode("allowlist", allowlist="VIP@example.com")
    payload = _payload("vip@example.com")
    response = await gated_client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 201, response.text


# ---- disabled ------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_disabled_blocks_every_call(
    gated_client: AsyncClient,
    db_session: AsyncSession,
    restore_env: None,
) -> None:
    _set_mode("disabled")
    payload = _payload("public@example.com", invite_code="ANY-CODE-12345")
    response = await gated_client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.REGISTRATION_DISABLED
    user = await db_session.scalar(select(User).where(User.email == payload["email"]))
    assert user is None


# ---- startup hardening ---------------------------------------------------


def _set_production_env() -> None:
    class _RegistrationGateBillingProvider:
        name = "registration_gate_real_billing"

        def create_checkout_session(
            self,
            *,
            billing_account_id: uuid.UUID,
            plan_code: str,
            success_url: str,
            cancel_url: str,
        ) -> CheckoutSession:
            raise AssertionError("startup validation should not create checkout sessions")

        def cancel_subscription(self, *, external_subscription_id: str) -> None:
            raise AssertionError("startup validation should not cancel subscriptions")

    register_billing_provider(
        "registration_gate_real_billing", lambda _s: _RegistrationGateBillingProvider()
    )
    os.environ["APP_ENV"] = "production"
    os.environ["COOKIE_SECURE"] = "true"
    os.environ["COOKIE_SAMESITE"] = "strict"
    os.environ["COOKIE_HOST_PREFIX"] = "true"
    os.environ.pop("COOKIE_DOMAIN", None)
    os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"
    os.environ["MFA_SECRET_KEY"] = "x" * 32
    os.environ["CSRF_SECRET"] = "y" * 32
    os.environ["EMAIL_DELIVERY_PROVIDER"] = "postmark"
    os.environ["EMAIL_VERIFICATION_FROM_ADDRESS"] = "no-reply@nextballup.com"
    os.environ["POSTMARK_SERVER_TOKEN"] = "postmark-token-for-registration-gate-tests"
    os.environ["BILLING_PROVIDER"] = "registration_gate_real_billing"
    os.environ["FRONTEND_APP_URL"] = "https://beta.nextballup.com"
    os.environ["DATABASE_URL_RUNTIME"] = "postgresql+asyncpg://app:app@localhost:5432/db"
    # Local .env may set CV_DEMO_PREVIEW_ENABLED=true for dev convenience; the
    # validator refuses that outside development/test, so force it off here.
    os.environ["CV_DEMO_PREVIEW_ENABLED"] = "false"


def test_startup_refuses_open_registration_in_production(
    restore_env: None,
) -> None:
    """Production / staging deploys may not boot with open registration —
    public/alpha/beta channels must be invite_only, allowlist, or disabled.
    """
    from nextballup_api.main import _validate_startup_secrets

    _set_production_env()
    os.environ["REGISTRATION_MODE"] = "open"
    reload_settings()
    with pytest.raises(RuntimeError, match="REGISTRATION_MODE"):
        _validate_startup_secrets()


def test_startup_refuses_invite_only_without_codes_in_production(
    restore_env: None,
) -> None:
    from nextballup_api.main import _validate_startup_secrets

    _set_production_env()
    os.environ["REGISTRATION_MODE"] = "invite_only"
    os.environ["REGISTRATION_INVITE_CODES"] = ""
    reload_settings()
    with pytest.raises(RuntimeError, match="REGISTRATION_INVITE_CODES"):
        _validate_startup_secrets()


def test_startup_accepts_invite_only_with_codes_in_production(
    restore_env: None,
) -> None:
    from nextballup_api.main import _validate_startup_secrets

    _set_production_env()
    os.environ["REGISTRATION_MODE"] = "invite_only"
    os.environ["REGISTRATION_INVITE_CODES"] = "PILOT-CODE-AAAA"
    reload_settings()
    # No raise expected.
    _validate_startup_secrets()


# ---- alpha channel (APP_ENV=staging) hardening ---------------------------


def _set_alpha_staging_env() -> None:
    """alpha.nextballup.com runs APP_ENV=staging; same hardening as production."""
    _set_production_env()
    os.environ["APP_ENV"] = "staging"
    os.environ["FRONTEND_APP_URL"] = "https://alpha.nextballup.com"


def test_alpha_staging_refuses_open_registration(restore_env: None) -> None:
    from nextballup_api.main import _validate_startup_secrets

    _set_alpha_staging_env()
    os.environ["REGISTRATION_MODE"] = "open"
    reload_settings()
    with pytest.raises(RuntimeError, match="REGISTRATION_MODE"):
        _validate_startup_secrets()


def test_alpha_staging_accepts_allowlist_with_emails(restore_env: None) -> None:
    from nextballup_api.main import _validate_startup_secrets

    _set_alpha_staging_env()
    os.environ["REGISTRATION_MODE"] = "allowlist"
    os.environ["REGISTRATION_EMAIL_ALLOWLIST"] = "alpha-coach-1@example.com"
    reload_settings()
    _validate_startup_secrets()


def test_alpha_staging_refuses_allowlist_without_emails(restore_env: None) -> None:
    from nextballup_api.main import _validate_startup_secrets

    _set_alpha_staging_env()
    os.environ["REGISTRATION_MODE"] = "allowlist"
    os.environ["REGISTRATION_EMAIL_ALLOWLIST"] = ""
    reload_settings()
    with pytest.raises(RuntimeError, match="REGISTRATION_EMAIL_ALLOWLIST"):
        _validate_startup_secrets()


def test_alpha_staging_refuses_demo_preview_bridge(restore_env: None) -> None:
    """The demo-preview bridge shells into the sibling training repo and is a
    development affordance only. It must stay disabled on alpha.nextballup.com
    so a misconfigured operator cannot expose pre-commercial CV previews."""
    from nextballup_api.main import _validate_startup_secrets

    _set_alpha_staging_env()
    os.environ["REGISTRATION_MODE"] = "invite_only"
    os.environ["REGISTRATION_INVITE_CODES"] = "ALPHA-CODE-AAAA"
    os.environ["CV_DEMO_PREVIEW_ENABLED"] = "true"
    reload_settings()
    with pytest.raises(RuntimeError, match="CV_DEMO_PREVIEW_ENABLED"):
        _validate_startup_secrets()


# ---- defaults are still backward-compatible ------------------------------


def test_default_mode_is_open(restore_env: None) -> None:
    settings = get_settings()
    assert settings.registration_mode == "open"
    assert settings.is_registration_invite_required() is False
    assert settings.is_registration_disabled() is False
    assert settings.is_registration_email_allowlisted("any@example.com") is True
