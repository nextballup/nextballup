"""TOTP MFA scaffold coverage.

Coverage:
    * setup → confirm flips MFA on, returns plaintext recovery codes once
    * unconfirmed setup can be re-issued without 409
    * already-confirmed setup is rejected with 409
    * disable requires correct password AND a valid code (TOTP or recovery)
    * recovery code is single-use; replay rejected
    * status reflects enrollment / confirmation / remaining recovery codes
    * setup gated to admin/coach roles only
    * encrypt/decrypt round-trip + TOTP verify pure-function tests
"""

from __future__ import annotations

import base64
import secrets
import time
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from nextballup_api.security.mfa import (
    decrypt_secret,
    encrypt_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    totp_now,
    verify_totp_code,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import ErrorCode
from nextballup_core.settings import get_settings
from nextballup_db.models.mfa import MfaRecoveryCode, UserTotpSecret
from nextballup_db.models.user import User

API = "/api/v1"


def _coach_payload(email: str = "mfa-coach@example.com") -> dict[str, object]:
    return {
        "email": email,
        "password": "Password1!",
        "full_name": "MFA Coach",
        "role": "coach",
    }


def _player_payload(email: str = "mfa-player@example.com") -> dict[str, object]:
    return {
        "email": email,
        "password": "Password1!",
        "full_name": "MFA Player",
        "role": "player",
    }


async def _attach_confirmed_totp(
    db_session: AsyncSession,
    *,
    email: str,
) -> tuple[str, User]:
    settings = get_settings()
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    secret = generate_totp_secret()
    db_session.add(
        UserTotpSecret(
            user_id=user.id,
            secret_ciphertext=encrypt_secret(
                secret,
                master_key=settings.effective_mfa_secret_key(),
            ),
            cipher="aes-gcm-pbkdf2",
            issuer_label=settings.mfa_totp_issuer,
            account_label=email,
            confirmed_at=datetime.now(tz=UTC),
        )
    )
    await db_session.commit()
    return secret, user


@pytest.mark.asyncio(loop_scope="session")
async def test_setup_then_confirm_returns_recovery_codes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    register = await client.post(f"{API}/auth/register", json=_coach_payload())
    assert register.status_code == 201

    setup = await client.post(f"{API}/auth/mfa/totp/setup", json={})
    assert setup.status_code == 201, setup.text
    body = setup.json()
    assert body["digits"] >= 6
    assert body["provisioning_uri"].startswith("otpauth://totp/")
    secret = body["secret_b32"]

    settings = get_settings()
    assert f"period={settings.mfa_totp_step_seconds}" in body["provisioning_uri"]
    code = totp_now(
        secret_b32=secret,
        step_seconds=settings.mfa_totp_step_seconds,
        digits=settings.mfa_totp_digits,
    )

    confirm = await client.post(f"{API}/auth/mfa/totp/confirm", json={"code": code})
    assert confirm.status_code == 200, confirm.text
    confirm_body = confirm.json()
    codes = confirm_body["recovery_codes"]
    assert len(codes) == settings.mfa_recovery_code_count

    # The DB now has the hashed codes — none of the plaintexts should match
    # any other plaintext (uniqueness) and all should be retrievable as
    # hashed rows for the user.
    user = await db_session.scalar(select(User).where(User.email == _coach_payload()["email"]))
    assert user is not None
    rows = (
        await db_session.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    ).all()
    assert len(rows) == settings.mfa_recovery_code_count


@pytest.mark.asyncio(loop_scope="session")
async def test_setup_re_issuance_before_confirm_is_allowed(client: AsyncClient) -> None:
    await client.post(f"{API}/auth/register", json=_coach_payload(email="re@example.com"))
    first = await client.post(f"{API}/auth/mfa/totp/setup", json={})
    assert first.status_code == 201
    second = await client.post(f"{API}/auth/mfa/totp/setup", json={})
    assert second.status_code == 201
    assert first.json()["secret_b32"] != second.json()["secret_b32"]


@pytest.mark.asyncio(loop_scope="session")
async def test_setup_after_confirm_rejected(
    client: AsyncClient,
) -> None:
    await client.post(f"{API}/auth/register", json=_coach_payload(email="dup@example.com"))
    setup = await client.post(f"{API}/auth/mfa/totp/setup", json={})
    assert setup.status_code == 201
    settings = get_settings()
    code = totp_now(
        secret_b32=setup.json()["secret_b32"],
        step_seconds=settings.mfa_totp_step_seconds,
        digits=settings.mfa_totp_digits,
    )
    confirm = await client.post(f"{API}/auth/mfa/totp/confirm", json={"code": code})
    assert confirm.status_code == 200

    second = await client.post(f"{API}/auth/mfa/totp/setup", json={})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == ErrorCode.MFA_ALREADY_ENROLLED


@pytest.mark.asyncio(loop_scope="session")
async def test_disable_requires_password_and_factor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _coach_payload(email="disable@example.com")
    await client.post(f"{API}/auth/register", json=payload)
    setup = await client.post(f"{API}/auth/mfa/totp/setup", json={})
    secret = setup.json()["secret_b32"]
    settings = get_settings()
    confirm = await client.post(
        f"{API}/auth/mfa/totp/confirm",
        json={
            "code": totp_now(
                secret_b32=secret,
                step_seconds=settings.mfa_totp_step_seconds,
                digits=settings.mfa_totp_digits,
            )
        },
    )
    assert confirm.status_code == 200
    recovery = confirm.json()["recovery_codes"][0]

    # Wrong password → 401, no MFA change
    bad_pw = await client.post(
        f"{API}/auth/mfa/totp/disable",
        json={"password": "Wrong1!", "code": recovery},
    )
    assert bad_pw.status_code == 401

    # Correct password + invalid code → 401
    bad_code = await client.post(
        f"{API}/auth/mfa/totp/disable",
        json={"password": payload["password"], "code": "000000"},
    )
    assert bad_code.status_code == 401

    # Correct password + recovery code → 204, recovery code is now used
    ok = await client.post(
        f"{API}/auth/mfa/totp/disable",
        json={"password": payload["password"], "code": recovery},
    )
    assert ok.status_code == 204

    user = await db_session.scalar(select(User).where(User.email == "disable@example.com"))
    assert user is not None
    record = await db_session.scalar(
        select(UserTotpSecret).where(UserTotpSecret.user_id == user.id)
    )
    assert record is not None
    assert record.disabled_at is not None

    # Replaying the same recovery code is rejected (already used).
    again = await client.post(
        f"{API}/auth/mfa/totp/disable",
        json={"password": payload["password"], "code": recovery},
    )
    # Disable requires currently-enabled MFA — second call hits the
    # "MFA not currently enabled" branch first, so the response is 400 with
    # the corresponding error code.
    assert again.status_code in (400, 401)


@pytest.mark.asyncio(loop_scope="session")
async def test_player_role_blocked_from_setup(client: AsyncClient) -> None:
    await client.post(f"{API}/auth/register", json=_player_payload())
    response = await client.post(f"{API}/auth/mfa/totp/setup", json={})
    assert response.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_status_reports_enrollment_state(client: AsyncClient) -> None:
    await client.post(f"{API}/auth/register", json=_coach_payload(email="stat@example.com"))
    pre = await client.get(f"{API}/auth/mfa/status")
    assert pre.status_code == 200
    assert pre.json() == {
        "enrolled": False,
        "confirmed": False,
        "last_used_at": None,
        "remaining_recovery_codes": 0,
    }
    setup = await client.post(f"{API}/auth/mfa/totp/setup", json={})
    settings = get_settings()
    await client.post(
        f"{API}/auth/mfa/totp/confirm",
        json={
            "code": totp_now(
                secret_b32=setup.json()["secret_b32"],
                step_seconds=settings.mfa_totp_step_seconds,
                digits=settings.mfa_totp_digits,
            )
        },
    )
    post_resp = await client.get(f"{API}/auth/mfa/status")
    assert post_resp.status_code == 200
    post = post_resp.json()
    assert post["enrolled"] is True
    assert post["confirmed"] is True
    assert post["remaining_recovery_codes"] == settings.mfa_recovery_code_count


@pytest.mark.asyncio(loop_scope="session")
async def test_login_requires_mfa_code_for_confirmed_enrollment(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    payload = _coach_payload(email="login-mfa-required@example.com")
    await client.post(f"{API}/auth/register", json=payload)
    email = str(payload["email"])
    await _attach_confirmed_totp(db_session, email=email)

    login = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": payload["password"]},
    )

    assert login.status_code == 401
    assert login.json()["error"]["code"] == ErrorCode.MFA_REQUIRED


@pytest.mark.asyncio(loop_scope="session")
async def test_login_accepts_totp_and_rejects_replay(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    payload = _coach_payload(email="login-mfa-totp@example.com")
    await client.post(f"{API}/auth/register", json=payload)
    email = str(payload["email"])
    secret, _ = await _attach_confirmed_totp(db_session, email=email)
    settings = get_settings()
    code = totp_now(
        secret_b32=secret,
        step_seconds=settings.mfa_totp_step_seconds,
        digits=settings.mfa_totp_digits,
    )

    ok = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": payload["password"], "mfa_code": code},
    )
    replay = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": payload["password"], "mfa_code": code},
    )

    assert ok.status_code == 200, ok.text
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == ErrorCode.MFA_INVALID_CODE


@pytest.mark.asyncio(loop_scope="session")
async def test_login_accepts_recovery_code_once(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    payload = _coach_payload(email="login-mfa-recovery@example.com")
    await client.post(f"{API}/auth/register", json=payload)
    email = str(payload["email"])
    _, user = await _attach_confirmed_totp(db_session, email=email)
    settings = get_settings()
    recovery = generate_recovery_codes(1)[0]
    db_session.add(
        MfaRecoveryCode(
            user_id=user.id,
            code_hash=hash_recovery_code(
                recovery,
                pepper=settings.effective_mfa_secret_key(),
            ),
        )
    )
    await db_session.commit()

    ok = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": payload["password"], "mfa_code": recovery},
    )
    replay = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": payload["password"], "mfa_code": recovery},
    )

    assert ok.status_code == 200, ok.text
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == ErrorCode.MFA_INVALID_CODE


# ---- Pure-function MFA primitives ------------------------------------------


def test_encrypt_decrypt_round_trip() -> None:
    secret = generate_totp_secret()
    blob = encrypt_secret(secret, master_key="local-test-key-please-rotate")
    assert blob != secret
    assert "." in blob
    back = decrypt_secret(blob, master_key="local-test-key-please-rotate")
    assert back == secret


def test_decrypt_with_wrong_key_raises() -> None:
    from cryptography.exceptions import InvalidTag

    blob = encrypt_secret("JBSWY3DPEHPK3PXP", master_key="key-a")
    with pytest.raises(InvalidTag):
        decrypt_secret(blob, master_key="key-b")


def test_totp_verify_accepts_current_step_and_rejects_replay() -> None:
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    now = int(time.time())
    code = totp_now(secret_b32=secret, step_seconds=30, digits=6, at=now)
    first = verify_totp_code(secret_b32=secret, code=code, step_seconds=30, digits=6, at=now)
    assert first.accepted is True
    assert first.matched_counter is not None
    replay = verify_totp_code(
        secret_b32=secret,
        code=code,
        step_seconds=30,
        digits=6,
        at=now,
        last_used_counter=first.matched_counter,
    )
    assert replay.accepted is False


def test_totp_verify_rejects_wrong_code() -> None:
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    out = verify_totp_code(
        secret_b32=secret, code="000000", step_seconds=30, digits=6, at=int(time.time())
    )
    assert out.accepted is False


def test_recovery_code_hashing_normalises_format() -> None:
    code = "abc def 123"
    hashed = hash_recovery_code(code)
    assert hashed == hash_recovery_code("ABCDEF123")
    assert hashed != hash_recovery_code("ABCDEF124")
    assert hash_recovery_code(code, pepper="tenant-secret-a") == hash_recovery_code(
        "ABCDEF123",
        pepper="tenant-secret-a",
    )
    assert hash_recovery_code(code, pepper="tenant-secret-a") != hash_recovery_code(
        code,
        pepper="tenant-secret-b",
    )


def test_generate_recovery_codes_unique_and_uppercase() -> None:
    codes = generate_recovery_codes(8)
    assert len(set(codes)) == 8
    for c in codes:
        assert c.isalnum()
        assert c == c.upper()
