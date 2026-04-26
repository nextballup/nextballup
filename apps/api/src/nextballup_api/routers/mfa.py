"""TOTP MFA enrollment / confirmation / disable endpoints.

Scope (intentional):
    POST /auth/mfa/totp/setup     -> mints a fresh secret + QR URI
    POST /auth/mfa/totp/confirm   -> proves possession + flips on
    POST /auth/mfa/totp/disable   -> requires password + active TOTP code
    GET  /auth/mfa/status         -> reports enrollment state

Confirmed TOTP enrollment is enforced by `/auth/login`; remember-device and
step-up auth remain intentionally out of scope for this endpoint group.

Today the endpoints are restricted to admin and coach roles — the people
most worth protecting. Player accounts can enroll later when product flows
need it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api.audit import write_audit
from nextballup_api.deps import get_app_settings, get_current_user, get_db
from nextballup_api.security.mfa import (
    decrypt_secret,
    encrypt_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    totp_provisioning_uri,
    verify_totp_code,
)
from nextballup_api.security.passwords import verify_password
from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import UserRole
from nextballup_core.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    InvalidCredentialsError,
)
from nextballup_core.settings import Settings
from nextballup_db.models.mfa import MfaRecoveryCode, UserTotpSecret
from nextballup_db.models.user import User

router = APIRouter(prefix="/auth/mfa", tags=["auth", "mfa"])

_MFA_ELIGIBLE_ROLES: frozenset[UserRole] = frozenset({UserRole.ADMIN, UserRole.COACH})


def _require_mfa_eligible(user: User) -> None:
    if user.role not in _MFA_ELIGIBLE_ROLES:
        raise ForbiddenError(
            "MFA enrollment is restricted to admin and coach roles",
            details={"required_roles": [r.value for r in _MFA_ELIGIBLE_ROLES]},
        )


# ---- Schemas ---------------------------------------------------------------


class MfaSetupResponse(BaseModel):
    secret_b32: str
    provisioning_uri: str
    digits: int
    step_seconds: int


class MfaConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=4, max_length=10)


class MfaConfirmResponse(BaseModel):
    confirmed_at: datetime
    recovery_codes: list[str] = Field(
        description="Plaintext recovery codes — shown once; not retrievable later."
    )


class MfaDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=4, max_length=10)


class MfaStatusResponse(BaseModel):
    enrolled: bool
    confirmed: bool
    last_used_at: datetime | None
    remaining_recovery_codes: int


# ---- Endpoints --------------------------------------------------------------


@router.post(
    "/totp/setup",
    response_model=MfaSetupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def setup_totp(
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> MfaSetupResponse:
    """Mint a fresh TOTP secret. If the user already has a *confirmed*
    enrollment we 409 to make accidental re-enrollment loud — disable first.
    A pending (unconfirmed) row is overwritten so a user can retry the QR
    scan without operator help.
    """
    _require_mfa_eligible(current_user)

    existing = await session.scalar(
        select(UserTotpSecret).where(UserTotpSecret.user_id == current_user.id)
    )
    if existing is not None and existing.confirmed_at is not None and existing.disabled_at is None:
        raise ConflictError(
            "TOTP MFA is already enrolled",
            code=ErrorCode.MFA_ALREADY_ENROLLED,
        )

    secret_b32 = generate_totp_secret()
    ciphertext = encrypt_secret(secret_b32, master_key=settings.effective_mfa_secret_key())
    if existing is not None:
        existing.secret_ciphertext = ciphertext
        existing.confirmed_at = None
        existing.disabled_at = None
        existing.last_used_at = None
        existing.account_label = current_user.email
        existing.issuer_label = settings.mfa_totp_issuer
        record = existing
    else:
        record = UserTotpSecret(
            user_id=current_user.id,
            secret_ciphertext=ciphertext,
            cipher="aes-gcm-pbkdf2",
            issuer_label=settings.mfa_totp_issuer,
            account_label=current_user.email,
        )
        session.add(record)

    await write_audit(
        session,
        action=AuditAction.USER_MFA_TOTP_ENROLLED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="user_totp_secret",
    )
    await session.commit()

    return MfaSetupResponse(
        secret_b32=secret_b32,
        provisioning_uri=totp_provisioning_uri(
            secret_b32=secret_b32,
            issuer=settings.mfa_totp_issuer,
            account=current_user.email,
            digits=settings.mfa_totp_digits,
            step_seconds=settings.mfa_totp_step_seconds,
        ),
        digits=settings.mfa_totp_digits,
        step_seconds=settings.mfa_totp_step_seconds,
    )


@router.post("/totp/confirm", response_model=MfaConfirmResponse)
async def confirm_totp(
    payload: MfaConfirmRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> MfaConfirmResponse:
    """Prove possession of the freshly-issued secret and flip it on.

    On success we mint and persist hashed recovery codes; the plaintext is
    returned exactly once. Subsequent confirms (e.g. after a disable) reset
    the recovery codes — old codes are no longer valid.
    """
    _require_mfa_eligible(current_user)
    record = await session.scalar(
        select(UserTotpSecret).where(UserTotpSecret.user_id == current_user.id)
    )
    if record is None or record.disabled_at is not None:
        raise AppError(
            "MFA enrollment has not been started",
            code=ErrorCode.MFA_NOT_ENROLLED,
            status_code=400,
        )
    secret_b32 = decrypt_secret(
        record.secret_ciphertext, master_key=settings.effective_mfa_secret_key()
    )
    last_counter: int | None = None
    if record.last_used_at is not None:
        last_counter = int(record.last_used_at.timestamp()) // settings.mfa_totp_step_seconds
    verification = verify_totp_code(
        secret_b32=secret_b32,
        code=payload.code.strip(),
        step_seconds=settings.mfa_totp_step_seconds,
        digits=settings.mfa_totp_digits,
        last_used_counter=last_counter,
    )
    if not verification.accepted or verification.matched_counter is None:
        raise InvalidCredentialsError(
            "Invalid TOTP code",
            code=ErrorCode.MFA_INVALID_CODE,
        )

    now = datetime.now(tz=UTC)
    record.confirmed_at = now
    record.last_used_at = datetime.fromtimestamp(
        verification.matched_counter * settings.mfa_totp_step_seconds, tz=UTC
    )

    # Reset any prior recovery codes — confirm starts a fresh batch.
    await session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == current_user.id))
    plaintexts = generate_recovery_codes(settings.mfa_recovery_code_count)
    for raw in plaintexts:
        session.add(
            MfaRecoveryCode(
                user_id=current_user.id,
                code_hash=hash_recovery_code(raw, pepper=settings.effective_mfa_secret_key()),
            )
        )

    await write_audit(
        session,
        action=AuditAction.USER_MFA_TOTP_CONFIRMED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="user_totp_secret",
    )
    await session.commit()
    return MfaConfirmResponse(confirmed_at=now, recovery_codes=plaintexts)


@router.post("/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_totp(
    payload: MfaDisableRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> None:
    """Requires the current password *and* a valid TOTP (or recovery) code.

    Disabling MFA is a sensitive action — we deliberately don't accept just
    one factor. A successful disable also bumps `session_version` so any
    other live session using the now-MFA-less account is forced through a
    fresh login.
    """
    _require_mfa_eligible(current_user)
    if not verify_password(payload.password, current_user.password_hash):
        raise InvalidCredentialsError("Password did not match")

    record = await session.scalar(
        select(UserTotpSecret).where(UserTotpSecret.user_id == current_user.id)
    )
    if record is None or record.confirmed_at is None or record.disabled_at is not None:
        raise AppError(
            "MFA is not currently enabled",
            code=ErrorCode.MFA_NOT_CONFIRMED,
            status_code=400,
        )

    secret_b32 = decrypt_secret(
        record.secret_ciphertext, master_key=settings.effective_mfa_secret_key()
    )
    last_counter: int | None = None
    if record.last_used_at is not None:
        last_counter = int(record.last_used_at.timestamp()) // settings.mfa_totp_step_seconds
    verification = verify_totp_code(
        secret_b32=secret_b32,
        code=payload.code.strip(),
        step_seconds=settings.mfa_totp_step_seconds,
        digits=settings.mfa_totp_digits,
        last_used_counter=last_counter,
    )
    if not verification.accepted:
        # Try recovery code as the second factor before giving up.
        code_hash = hash_recovery_code(
            payload.code.strip(),
            pepper=settings.effective_mfa_secret_key(),
        )
        recovery = await session.scalar(
            select(MfaRecoveryCode).where(
                MfaRecoveryCode.user_id == current_user.id,
                MfaRecoveryCode.code_hash == code_hash,
                MfaRecoveryCode.used_at.is_(None),
            )
        )
        if recovery is None:
            raise InvalidCredentialsError(
                "Invalid TOTP or recovery code",
                code=ErrorCode.MFA_INVALID_CODE,
            )
        recovery.used_at = datetime.now(tz=UTC)
        await write_audit(
            session,
            action=AuditAction.USER_MFA_RECOVERY_USED,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            resource_type="mfa_recovery_code",
            resource_id=recovery.id,
        )

    record.disabled_at = datetime.now(tz=UTC)
    current_user.session_version += 1

    await write_audit(
        session,
        action=AuditAction.USER_MFA_TOTP_DISABLED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="user_totp_secret",
    )
    await session.commit()


@router.get("/status", response_model=MfaStatusResponse)
async def mfa_status(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MfaStatusResponse:
    record = await session.scalar(
        select(UserTotpSecret).where(UserTotpSecret.user_id == current_user.id)
    )
    enrolled = record is not None and record.disabled_at is None
    confirmed = enrolled and record is not None and record.confirmed_at is not None
    remaining = 0
    if confirmed:
        remaining = int(
            await session.scalar(
                select(func.count())
                .select_from(MfaRecoveryCode)
                .where(
                    MfaRecoveryCode.user_id == current_user.id,
                    MfaRecoveryCode.used_at.is_(None),
                )
            )
            or 0
        )
    return MfaStatusResponse(
        enrolled=enrolled,
        confirmed=confirmed,
        last_used_at=record.last_used_at if record is not None else None,
        remaining_recovery_codes=remaining,
    )
