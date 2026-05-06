from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from nextballup_api.audit import write_audit
from nextballup_api.deps import get_app_settings, get_current_user, get_db
from nextballup_api.email_verification import (
    confirm_verification_token,
    deliver_verification_email,
    issue_verification_token,
)
from nextballup_api.password_reset import (
    consume_password_reset_token,
    deliver_password_reset_email,
    issue_password_reset_token,
)
from nextballup_api.request_meta import client_ip
from nextballup_api.security.cookies import clear_auth_cookies, set_auth_cookies
from nextballup_api.security.csrf import (
    clear_csrf_cookie,
    generate_csrf_token,
    set_csrf_cookie,
)
from nextballup_api.security.jwt import create_access_token, create_refresh_token, decode_token
from nextballup_api.security.mfa import decrypt_secret, hash_recovery_code, verify_totp_code
from nextballup_api.security.passwords import hash_password, verify_password
from nextballup_api.security.rate_limit import enforce_auth_rate_limit, enforce_rate_limit
from nextballup_api.tenant import set_user_context, set_user_role_context
from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import TeamRole
from nextballup_core.errors import (
    AppError,
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    InvalidCredentialsError,
)
from nextballup_core.schemas.auth import (
    LoginRequest,
    LoginResponse,
    PasswordResetConfirmRequest,
    PasswordResetConfirmResponse,
    PasswordResetRequest,
    PasswordResetRequestResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    RegistrationStatusResponse,
    TeamMembershipSummary,
    UserPublic,
)
from nextballup_core.schemas.compliance import (
    AccountDeleteResponse,
    AuditEventExport,
    BillingAccountExport,
    CspReportExport,
    EmailVerificationTokenExport,
    MfaEnrollmentExport,
    PasswordResetTokenExport,
    RefreshSessionExport,
    TeamMembershipExport,
    TeamPrivacyConsentExport,
    UsageEventExport,
    UserDataExport,
    UserProfileExport,
    VideoSummaryExport,
)
from nextballup_core.schemas.email_verification import (
    ConfirmEmailVerificationRequest,
    ConfirmEmailVerificationResponse,
    EmailVerificationStatusResponse,
    RequestEmailVerificationRequest,
    RequestEmailVerificationResponse,
)
from nextballup_core.settings import Settings
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.auth import RefreshSession
from nextballup_db.models.billing import BillingAccount, UsageEvent
from nextballup_db.models.csp import CspReport
from nextballup_db.models.email_verification import EmailVerificationToken
from nextballup_db.models.mfa import MfaRecoveryCode, UserTotpSecret
from nextballup_db.models.password_reset import PasswordResetToken
from nextballup_db.models.team import TeamMembership, TeamPrivacyConsent
from nextballup_db.models.user import User
from nextballup_db.models.video import Video

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_public(user: User) -> UserPublic:
    teams = [
        TeamMembershipSummary(
            id=m.team_id,
            name=m.team.name,
            role_in_team=m.team_role.value,
        )
        for m in user.team_memberships
        if m.is_active and m.team is not None and m.team.deleted_at is None
    ]
    return UserPublic(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        teams=teams,
    )


def _team_ids(user: User) -> list[uuid.UUID]:
    return [
        m.team_id
        for m in user.team_memberships
        if m.is_active and m.team is not None and m.team.deleted_at is None
    ]


def _issue_tokens(
    user: User,
    *,
    settings: Settings,
    team_ids: list[uuid.UUID] | None = None,
    refresh_jti: str | None = None,
) -> tuple[str, str, str, datetime]:
    resolved_team_ids = team_ids if team_ids is not None else _team_ids(user)
    resolved_refresh_jti = refresh_jti or str(uuid.uuid4())
    access_token = create_access_token(
        subject=user.id,
        role=user.role,
        session_version=user.session_version,
        team_ids=resolved_team_ids,
        settings=settings,
    )
    refresh_token = create_refresh_token(
        subject=user.id,
        role=user.role,
        session_version=user.session_version,
        team_ids=resolved_team_ids,
        settings=settings,
        jti=resolved_refresh_jti,
    )
    refresh_expires_at = datetime.now(tz=UTC) + timedelta(days=settings.refresh_token_expire_days)
    return access_token, refresh_token, resolved_refresh_jti, refresh_expires_at


def _hash_refresh_jti(jti: str) -> str:
    return sha256(jti.encode("utf-8")).hexdigest()


def _request_user_agent(request: Request) -> str | None:
    user_agent = request.headers.get("user-agent")
    if not user_agent:
        return None
    return user_agent[:500]


async def _create_refresh_session(
    session: AsyncSession,
    *,
    user: User,
    jti: str,
    expires_at: datetime,
    request: Request,
    settings: Settings,
) -> RefreshSession:
    refresh_session = RefreshSession(
        user_id=user.id,
        jti_hash=_hash_refresh_jti(jti),
        expires_at=expires_at,
        ip_address=client_ip(request, settings=settings),
        user_agent=_request_user_agent(request),
    )
    session.add(refresh_session)
    await session.flush()
    return refresh_session


async def _revoke_refresh_sessions(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    reason: str,
) -> None:
    await session.execute(
        update(RefreshSession)
        .where(
            RefreshSession.user_id == user_id,
            RefreshSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(tz=UTC), revoked_reason=reason)
    )


async def _require_mfa_for_login_if_enabled(
    session: AsyncSession,
    *,
    user: User,
    code: str | None,
    request: Request,
    settings: Settings,
) -> None:
    record = await session.scalar(
        select(UserTotpSecret)
        .where(
            UserTotpSecret.user_id == user.id,
            UserTotpSecret.confirmed_at.is_not(None),
            UserTotpSecret.disabled_at.is_(None),
        )
        .with_for_update()
    )
    if record is None:
        return
    if not code:
        await write_audit(
            session,
            action=AuditAction.USER_LOGIN_FAILED,
            request=request,
            actor_email=user.email,
            actor_user_id=user.id,
            extra={"reason": ErrorCode.MFA_REQUIRED},
        )
        await session.commit()
        raise AuthenticationError(
            "MFA code is required",
            code=ErrorCode.MFA_REQUIRED,
            details={"mfa_required": True},
        )

    submitted = code.strip()
    secret_b32 = decrypt_secret(
        record.secret_ciphertext, master_key=settings.effective_mfa_secret_key()
    )
    last_counter: int | None = None
    if record.last_used_at is not None:
        last_counter = int(record.last_used_at.timestamp()) // settings.mfa_totp_step_seconds
    verification = verify_totp_code(
        secret_b32=secret_b32,
        code=submitted,
        step_seconds=settings.mfa_totp_step_seconds,
        digits=settings.mfa_totp_digits,
        last_used_counter=last_counter,
    )
    now = datetime.now(tz=UTC)
    if verification.accepted and verification.matched_counter is not None:
        record.last_used_at = datetime.fromtimestamp(
            verification.matched_counter * settings.mfa_totp_step_seconds,
            tz=UTC,
        )
        return

    code_hash = hash_recovery_code(submitted, pepper=settings.effective_mfa_secret_key())
    recovery = await session.scalar(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == user.id,
            MfaRecoveryCode.code_hash == code_hash,
            MfaRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if recovery is not None:
        recovery.used_at = now
        await write_audit(
            session,
            action=AuditAction.USER_MFA_RECOVERY_USED,
            request=request,
            actor_user_id=user.id,
            actor_email=user.email,
            resource_type="mfa_recovery_code",
            resource_id=recovery.id,
        )
        return

    await write_audit(
        session,
        action=AuditAction.USER_LOGIN_FAILED,
        request=request,
        actor_email=user.email,
        actor_user_id=user.id,
        extra={"reason": ErrorCode.MFA_INVALID_CODE},
    )
    await session.commit()
    raise InvalidCredentialsError("Invalid MFA code", code=ErrorCode.MFA_INVALID_CODE)


async def _load_user_with_memberships(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> User | None:
    result = await session.execute(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.team_memberships).selectinload(TeamMembership.team))
    )
    return result.scalar_one_or_none()


async def _user_from_refresh_token(
    token: str,
    *,
    session: AsyncSession,
    settings: Settings,
) -> tuple[User, RefreshSession]:
    claims = decode_token(token, expected_type="refresh", settings=settings)
    try:
        user_id = uuid.UUID(claims["sub"])
    except ValueError as exc:
        raise AuthenticationError("Malformed token subject") from exc
    jti = claims.get("jti")
    if not isinstance(jti, str):
        raise AuthenticationError("Malformed token id")

    await set_user_context(session, user_id)
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User is no longer active")
    if claims["sv"] != user.session_version:
        raise AuthenticationError("Session has been invalidated")
    await set_user_role_context(session, user.role)
    user = await _load_user_with_memberships(session, user_id=user_id)
    if user is None:  # pragma: no cover - guarded by the point lookup above
        raise AuthenticationError("User is no longer active")

    refresh_session = await session.scalar(
        select(RefreshSession)
        .where(
            RefreshSession.user_id == user.id,
            RefreshSession.jti_hash == _hash_refresh_jti(jti),
        )
        .with_for_update()
    )
    now = datetime.now(tz=UTC)
    if refresh_session is None:
        raise AuthenticationError("Refresh session was not found")
    if refresh_session.revoked_at is not None:
        await _revoke_refresh_sessions(session, user_id=user.id, reason="replay_detected")
        user.session_version += 1
        raise AuthenticationError("Refresh session has been revoked")
    if refresh_session.expires_at <= now:
        refresh_session.revoked_at = now
        refresh_session.revoked_reason = "expired"
        raise AuthenticationError("Refresh session has expired")
    return user, refresh_session


async def _enforce_registration_gate(
    payload: RegisterRequest,
    *,
    request: Request,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Reject registration when the deployment channel says it should be closed.

    Audit the rejection (without logging the submitted invite code) before
    raising so denied attempts are observable in the audit log.
    """
    email_normalized = payload.email.lower()
    if settings.is_registration_disabled():
        await write_audit(
            session,
            action=AuditAction.USER_REGISTER_FAILED,
            request=request,
            actor_email=email_normalized,
            extra={"reason": ErrorCode.REGISTRATION_DISABLED},
        )
        await session.commit()
        raise ForbiddenError(
            "Registration is disabled on this deployment",
            code=ErrorCode.REGISTRATION_DISABLED,
        )
    if settings.is_registration_invite_required():
        if not payload.invite_code:
            await write_audit(
                session,
                action=AuditAction.USER_REGISTER_FAILED,
                request=request,
                actor_email=email_normalized,
                extra={"reason": ErrorCode.REGISTRATION_INVITE_REQUIRED},
            )
            await session.commit()
            raise ForbiddenError(
                "An invite code is required to register on this deployment",
                code=ErrorCode.REGISTRATION_INVITE_REQUIRED,
            )
        if not settings.is_valid_registration_invite_code(payload.invite_code):
            await write_audit(
                session,
                action=AuditAction.USER_REGISTER_FAILED,
                request=request,
                actor_email=email_normalized,
                extra={"reason": ErrorCode.REGISTRATION_INVITE_INVALID},
            )
            await session.commit()
            raise ForbiddenError(
                "Invite code is not valid",
                code=ErrorCode.REGISTRATION_INVITE_INVALID,
            )
    if not settings.is_registration_email_allowlisted(email_normalized):
        await write_audit(
            session,
            action=AuditAction.USER_REGISTER_FAILED,
            request=request,
            actor_email=email_normalized,
            extra={"reason": ErrorCode.REGISTRATION_NOT_ALLOWLISTED},
        )
        await session.commit()
        raise ForbiddenError(
            "This email is not on the registration allowlist",
            code=ErrorCode.REGISTRATION_NOT_ALLOWLISTED,
        )


@router.get(
    "/registration/status",
    response_model=RegistrationStatusResponse,
)
async def registration_status(
    response: Response,
    settings: Settings = Depends(get_app_settings),
) -> RegistrationStatusResponse:
    """Surface the current registration gate so the frontend can render the
    right UI on the public landing and register pages without leaking the
    configured codes or allowlist."""
    response.headers["Cache-Control"] = "no-store"
    return RegistrationStatusResponse(
        mode=settings.registration_mode,
        invite_code_required=settings.is_registration_invite_required(),
        is_open_to_public=settings.registration_mode == "open",
    )


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> RegisterResponse:
    email_normalized = payload.email.lower()
    await enforce_auth_rate_limit(
        request=request,
        settings=settings,
        scope="auth_register",
        subject=email_normalized,
    )
    await enforce_rate_limit(
        request=request,
        settings=settings,
        scope="auth_register_ip",
        subject="registration",
        max_attempts=settings.auth_rate_limit_attempts,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )
    await _enforce_registration_gate(payload, request=request, session=session, settings=settings)

    existing = await session.scalar(select(User.id).where(User.email == email_normalized))
    if existing is not None:
        await write_audit(
            session,
            action=AuditAction.USER_REGISTER_FAILED,
            request=request,
            actor_email=email_normalized,
            extra={"reason": ErrorCode.EMAIL_TAKEN},
        )
        await session.commit()
        raise ConflictError(
            "An account with that email already exists",
            code=ErrorCode.EMAIL_TAKEN,
        )

    user = User(
        email=email_normalized,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        phone=payload.phone,
        institution=payload.institution,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Race with a concurrent registration on the same email.
        await session.rollback()
        await write_audit(
            session,
            action=AuditAction.USER_REGISTER_FAILED,
            request=request,
            actor_email=email_normalized,
            extra={"reason": ErrorCode.EMAIL_TAKEN},
        )
        await session.commit()
        raise ConflictError(
            "An account with that email already exists",
            code=ErrorCode.EMAIL_TAKEN,
        ) from exc

    access_token, refresh_token, refresh_jti, refresh_expires_at = _issue_tokens(
        user, settings=settings, team_ids=[]
    )
    await _create_refresh_session(
        session,
        user=user,
        jti=refresh_jti,
        expires_at=refresh_expires_at,
        request=request,
        settings=settings,
    )

    await write_audit(
        session,
        action=AuditAction.USER_REGISTER_SUCCEEDED,
        request=request,
        actor_user_id=user.id,
        actor_email=user.email,
        resource_type="user",
        resource_id=user.id,
        extra={"role": user.role.value},
    )
    await session.commit()
    await session.refresh(user)

    set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        settings=settings,
    )
    set_csrf_cookie(
        response,
        token=generate_csrf_token(settings=settings),
        settings=settings,
    )

    return RegisterResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        created_at=user.created_at,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> LoginResponse:
    email_normalized = payload.email.lower()
    await enforce_auth_rate_limit(
        request=request,
        settings=settings,
        scope="auth_login",
        subject=email_normalized,
    )
    user = await session.scalar(select(User).where(User.email == email_normalized))

    if user is None or not verify_password(payload.password, user.password_hash):
        await write_audit(
            session,
            action=AuditAction.USER_LOGIN_FAILED,
            request=request,
            actor_email=email_normalized,
            actor_user_id=user.id if user else None,
            extra={"reason": ErrorCode.INVALID_CREDENTIALS},
        )
        await session.commit()
        raise InvalidCredentialsError("Invalid email or password")

    if not user.is_active:
        await write_audit(
            session,
            action=AuditAction.USER_LOGIN_FAILED,
            request=request,
            actor_email=email_normalized,
            actor_user_id=user.id,
            extra={"reason": ErrorCode.USER_INACTIVE},
        )
        await session.commit()
        raise AuthenticationError("Account is disabled", code=ErrorCode.USER_INACTIVE)

    await set_user_context(session, user.id)
    await set_user_role_context(session, user.role)
    user = await _load_user_with_memberships(session, user_id=user.id)
    if user is None:  # pragma: no cover - guarded by previous query
        raise AuthenticationError("User is no longer active")

    await _require_mfa_for_login_if_enabled(
        session,
        user=user,
        code=payload.mfa_code,
        request=request,
        settings=settings,
    )

    access_token, refresh_token, refresh_jti, refresh_expires_at = _issue_tokens(
        user, settings=settings
    )
    await _create_refresh_session(
        session,
        user=user,
        jti=refresh_jti,
        expires_at=refresh_expires_at,
        request=request,
        settings=settings,
    )

    await write_audit(
        session,
        action=AuditAction.USER_LOGIN_SUCCEEDED,
        request=request,
        actor_user_id=user.id,
        actor_email=user.email,
        resource_type="user",
        resource_id=user.id,
    )
    await session.commit()

    set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        settings=settings,
    )
    set_csrf_cookie(
        response,
        token=generate_csrf_token(settings=settings),
        settings=settings,
    )

    return LoginResponse(user=_user_public(user))


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> RefreshResponse:
    # `payload` is required to keep FastAPI enforcing `extra="forbid"` — if a
    # caller passes a legacy `refresh_token` field it's rejected with 422
    # rather than silently ignored.
    del payload
    # Cookie-only: the refresh JWT never rides in the JSON body, so an
    # attacker who can read the response (XSS, cached network capture) never
    # sees a reusable token.
    token = request.cookies.get(f"__Host-{settings.cookie_refresh_name}") or request.cookies.get(
        settings.cookie_refresh_name
    )
    if not token:
        await write_audit(
            session,
            action=AuditAction.USER_REFRESH_FAILED,
            request=request,
            extra={"reason": ErrorCode.UNAUTHENTICATED},
        )
        await session.commit()
        raise AuthenticationError("Missing refresh token")

    try:
        user, consumed_session = await _user_from_refresh_token(
            token, session=session, settings=settings
        )
    except AuthenticationError as exc:
        await write_audit(
            session,
            action=AuditAction.USER_REFRESH_FAILED,
            request=request,
            extra={"reason": ErrorCode.UNAUTHENTICATED},
        )
        await session.commit()
        raise exc

    consumed_session.revoked_at = datetime.now(tz=UTC)
    consumed_session.revoked_reason = "rotated"
    await session.flush()
    try:
        access_token, refresh_token, refresh_jti, refresh_expires_at = _issue_tokens(
            user, settings=settings
        )
        replacement = await _create_refresh_session(
            session,
            user=user,
            jti=refresh_jti,
            expires_at=refresh_expires_at,
            request=request,
            settings=settings,
        )
    except Exception:
        await session.rollback()
        raise
    consumed_session.replaced_by_session_id = replacement.id
    await write_audit(
        session,
        action=AuditAction.USER_REFRESH_SUCCEEDED,
        request=request,
        actor_user_id=user.id,
        actor_email=user.email,
        resource_type="user",
        resource_id=user.id,
    )
    await session.commit()
    set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        settings=settings,
    )
    set_csrf_cookie(
        response,
        token=generate_csrf_token(settings=settings),
        settings=settings,
    )
    return RefreshResponse(refreshed_at=datetime.now(tz=UTC))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    current_user: User = Depends(get_current_user),
) -> Response:
    await write_audit(
        session,
        action=AuditAction.USER_LOGOUT,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="user",
        resource_id=current_user.id,
    )
    await _revoke_refresh_sessions(session, user_id=current_user.id, reason="logout")
    current_user.session_version += 1
    await session.commit()
    clear_auth_cookies(response, settings=settings)
    clear_csrf_cookie(response, settings=settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/password/forgot",
    response_model=PasswordResetRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> PasswordResetRequestResponse:
    email_normalized = payload.email.lower()
    await enforce_rate_limit(
        request=request,
        settings=settings,
        scope="password_reset_request",
        subject=email_normalized,
        max_attempts=settings.password_reset_request_rate_attempts,
        window_seconds=settings.password_reset_request_rate_window_seconds,
    )
    user = await session.scalar(select(User).where(User.email == email_normalized))
    requested_at = datetime.now(tz=UTC)
    if user is None or not user.is_active:
        await write_audit(
            session,
            action=AuditAction.USER_PASSWORD_RESET_REQUESTED,
            request=request,
            actor_email=email_normalized,
            extra={"result": "accepted"},
        )
        await session.commit()
        return PasswordResetRequestResponse(
            requested_at=requested_at,
            delivery=settings.email_delivery_provider,
        )

    issued = await issue_password_reset_token(
        session,
        user=user,
        request=request,
        settings=settings,
    )
    await write_audit(
        session,
        action=AuditAction.USER_PASSWORD_RESET_REQUESTED,
        request=request,
        actor_user_id=user.id,
        actor_email=user.email,
        resource_type="password_reset_token",
        resource_id=issued.record.id,
        extra={
            "expires_at": issued.expires_at.isoformat(),
            "provider": settings.email_delivery_provider,
        },
    )
    await session.commit()

    try:
        deliver_password_reset_email(user=user, raw_token=issued.raw_token, settings=settings)
        await write_audit(
            session,
            action=AuditAction.USER_PASSWORD_RESET_SENT,
            request=request,
            actor_user_id=user.id,
            actor_email=user.email,
            resource_type="password_reset_token",
            resource_id=issued.record.id,
            extra={"provider": settings.email_delivery_provider},
        )
    except Exception:
        failure_at = datetime.now(tz=UTC)
        await session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.id == issued.record.id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=failure_at)
        )
        await write_audit(
            session,
            action=AuditAction.USER_PASSWORD_RESET_REJECTED,
            request=request,
            actor_user_id=user.id,
            actor_email=user.email,
            resource_type="password_reset_token",
            resource_id=issued.record.id,
            extra={"reason": "delivery_failed", "provider": settings.email_delivery_provider},
        )
    await session.commit()
    return PasswordResetRequestResponse(
        requested_at=requested_at,
        delivery=settings.email_delivery_provider,
    )


@router.post("/password/reset", response_model=PasswordResetConfirmResponse)
async def reset_password(
    payload: PasswordResetConfirmRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> PasswordResetConfirmResponse:
    await enforce_rate_limit(
        request=request,
        settings=settings,
        scope="password_reset_confirm",
        subject="password_reset_confirm",
        max_attempts=settings.password_reset_confirm_rate_attempts,
        window_seconds=settings.password_reset_confirm_rate_window_seconds,
    )
    consumed, reason = await consume_password_reset_token(
        session,
        raw_token=payload.token,
        request=request,
        settings=settings,
    )
    if consumed is None:
        await write_audit(
            session,
            action=AuditAction.USER_PASSWORD_RESET_REJECTED,
            request=request,
            extra={"reason": reason or "invalid"},
        )
        await session.commit()
        if reason == "expired":
            raise AppError(
                "Password reset link has expired",
                code=ErrorCode.PASSWORD_RESET_TOKEN_EXPIRED,
                status_code=400,
            )
        if reason == "used":
            raise AppError(
                "Password reset link has already been used",
                code=ErrorCode.PASSWORD_RESET_TOKEN_USED,
                status_code=409,
            )
        raise AppError(
            "Password reset link is not valid",
            code=ErrorCode.PASSWORD_RESET_TOKEN_INVALID,
            status_code=400,
        )

    user = consumed.user
    user.password_hash = hash_password(payload.new_password)
    user.session_version += 1
    await _revoke_refresh_sessions(session, user_id=user.id, reason="password_reset")
    await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=consumed.reset_at)
    )
    await write_audit(
        session,
        action=AuditAction.USER_PASSWORD_RESET_SUCCEEDED,
        request=request,
        actor_user_id=user.id,
        actor_email=user.email,
        resource_type="user",
        resource_id=user.id,
    )
    await session.commit()
    clear_auth_cookies(response, settings=settings)
    clear_csrf_cookie(response, settings=settings)
    return PasswordResetConfirmResponse(reset_at=consumed.reset_at)


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return _user_public(current_user)


# ---- GET /auth/me/export (GDPR Art. 15 self-serve access) -----------------


@router.get("/me/export", response_model=UserDataExport)
async def export_my_data(
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserDataExport:
    """Return everything we hold about this user in a single JSON bundle.

    Scope is deliberately user-centric: rows keyed to the user (profile,
    memberships, audit-actor events, uploaded videos). Tenant-owned rows
    the user can merely *see* (teammates' videos, team-wide audit events)
    are not included — those belong in a separate tenant-owner export if
    we ever add one.
    """
    memberships_result = await session.execute(
        select(TeamMembership)
        .where(TeamMembership.user_id == current_user.id)
        .options(selectinload(TeamMembership.team))
    )
    memberships = memberships_result.scalars().all()

    videos_result = await session.execute(select(Video).where(Video.uploaded_by == current_user.id))
    videos = videos_result.scalars().all()

    audit_result = await session.execute(
        select(AuditLog)
        .where(AuditLog.actor_user_id == current_user.id)
        .order_by(AuditLog.created_at.desc())
    )
    audit_events = audit_result.scalars().all()

    refresh_result = await session.execute(
        select(RefreshSession)
        .where(RefreshSession.user_id == current_user.id)
        .order_by(RefreshSession.created_at.desc())
    )
    refresh_sessions = refresh_result.scalars().all()

    email_tokens_result = await session.execute(
        select(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == current_user.id)
        .order_by(EmailVerificationToken.created_at.desc())
    )
    email_tokens = email_tokens_result.scalars().all()

    password_reset_result = await session.execute(
        select(PasswordResetToken)
        .where(PasswordResetToken.user_id == current_user.id)
        .order_by(PasswordResetToken.created_at.desc())
    )
    password_reset_tokens = password_reset_result.scalars().all()

    totp_record = await session.scalar(
        select(UserTotpSecret).where(UserTotpSecret.user_id == current_user.id)
    )
    recovery_total = int(
        await session.scalar(
            select(func.count())
            .select_from(MfaRecoveryCode)
            .where(MfaRecoveryCode.user_id == current_user.id)
        )
        or 0
    )
    recovery_unused = int(
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

    member_team_ids = [m.team_id for m in memberships]
    owned_accounts = (
        await session.scalars(
            select(BillingAccount)
            .where(BillingAccount.owner_user_id == current_user.id)
            .order_by(BillingAccount.created_at.desc())
        )
    ).all()
    usage_events: list[UsageEvent] = []
    if member_team_ids:
        usage_events = list(
            (
                await session.scalars(
                    select(UsageEvent)
                    .where(UsageEvent.team_id.in_(member_team_ids))
                    .order_by(UsageEvent.occurred_at.desc())
                )
            ).all()
        )

    consents_recorded = (
        await session.scalars(
            select(TeamPrivacyConsent)
            .where(TeamPrivacyConsent.recorded_by == current_user.id)
            .order_by(TeamPrivacyConsent.effective_at.desc())
        )
    ).all()

    csp_reports = (
        await session.scalars(
            select(CspReport)
            .where(CspReport.user_id == current_user.id)
            .order_by(CspReport.received_at.desc())
        )
    ).all()

    bundle = UserDataExport(
        exported_at=datetime.now(tz=UTC),
        user=UserProfileExport.model_validate(current_user),
        team_memberships=[
            TeamMembershipExport(
                team_id=m.team_id,
                team_name=m.team.name if m.team else "",
                team_role=m.team_role.value,
                jersey_number=m.jersey_number,
                is_active=m.is_active,
                joined_at=m.joined_at,
            )
            for m in memberships
        ],
        videos_uploaded=[
            VideoSummaryExport(
                id=v.id,
                game_id=v.game_id,
                team_id=v.team_id,
                filename=v.filename,
                file_size_bytes=v.file_size_bytes,
                status=v.status.value,
                created_at=v.created_at,
            )
            for v in videos
        ],
        audit_events=[
            AuditEventExport(
                action=e.action,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                team_id=e.team_id,
                ip_address=str(e.ip_address) if e.ip_address else None,
                created_at=e.created_at,
                extra=e.extra,
            )
            for e in audit_events
        ],
        refresh_sessions=[
            RefreshSessionExport(
                id=s.id,
                created_at=s.created_at,
                expires_at=s.expires_at,
                revoked_at=s.revoked_at,
                revoked_reason=s.revoked_reason,
                replaced_by_session_id=s.replaced_by_session_id,
                ip_address=str(s.ip_address) if s.ip_address else None,
                user_agent=s.user_agent,
            )
            for s in refresh_sessions
        ],
        email_verification_tokens=[
            EmailVerificationTokenExport(
                id=t.id,
                created_at=t.created_at,
                expires_at=t.expires_at,
                used_at=t.used_at,
                requested_ip=str(t.requested_ip) if t.requested_ip else None,
                requested_user_agent=t.requested_user_agent,
                confirmed_ip=str(t.confirmed_ip) if t.confirmed_ip else None,
            )
            for t in email_tokens
        ],
        password_reset_tokens=[
            PasswordResetTokenExport(
                id=t.id,
                created_at=t.created_at,
                expires_at=t.expires_at,
                used_at=t.used_at,
                requested_ip=str(t.requested_ip) if t.requested_ip else None,
                requested_user_agent=t.requested_user_agent,
                reset_ip=str(t.reset_ip) if t.reset_ip else None,
            )
            for t in password_reset_tokens
        ],
        mfa=MfaEnrollmentExport(
            enrolled=totp_record is not None and totp_record.disabled_at is None,
            confirmed_at=totp_record.confirmed_at if totp_record is not None else None,
            disabled_at=totp_record.disabled_at if totp_record is not None else None,
            last_used_at=totp_record.last_used_at if totp_record is not None else None,
            recovery_codes_total=recovery_total,
            recovery_codes_unused=recovery_unused,
        ),
        billing_accounts_owned=[
            BillingAccountExport(
                id=a.id,
                name=a.name,
                status=a.status.value,
                billing_email=a.billing_email,
                deleted_at=a.deleted_at,
                created_at=a.created_at,
            )
            for a in owned_accounts
        ],
        usage_events_for_member_teams=[
            UsageEventExport(
                id=e.id,
                billing_account_id=e.billing_account_id,
                team_id=e.team_id,
                event_key=e.event_key,
                quantity=e.quantity,
                occurred_at=e.occurred_at,
                event_metadata=e.event_metadata,
            )
            for e in usage_events
        ],
        team_privacy_consents_recorded=[
            TeamPrivacyConsentExport(
                id=c.id,
                team_id=c.team_id,
                label=c.label,
                consent_source=c.consent_source,
                covers_video_uploads=c.covers_video_uploads,
                covers_cv_processing=c.covers_cv_processing,
                commercial_ml_training_allowed=c.commercial_ml_training_allowed,
                minors_authorized=c.minors_authorized,
                athlete_pii_authorized=c.athlete_pii_authorized,
                evidence_uri=c.evidence_uri,
                evidence_sha256=c.evidence_sha256,
                effective_at=c.effective_at,
                expires_at=c.expires_at,
                revoked_at=c.revoked_at,
            )
            for c in consents_recorded
        ],
        csp_reports_attributed=[
            CspReportExport(
                id=r.id,
                received_at=r.received_at,
                document_uri=r.document_uri,
                violated_directive=r.violated_directive,
                blocked_uri=r.blocked_uri,
                source_file=r.source_file,
                line_number=r.line_number,
                column_number=r.column_number,
                user_agent=r.user_agent,
                reporter_ip=str(r.reporter_ip) if r.reporter_ip else None,
            )
            for r in csp_reports
        ],
    )

    await write_audit(
        session,
        action=AuditAction.USER_DATA_EXPORTED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="user",
        resource_id=current_user.id,
        extra={
            "memberships": len(memberships),
            "videos": len(videos),
            "audit_events": len(audit_events),
            "refresh_sessions": len(refresh_sessions),
            "email_verification_tokens": len(email_tokens),
            "password_reset_tokens": len(password_reset_tokens),
            "usage_events": len(usage_events),
            "csp_reports": len(csp_reports),
        },
    )
    await session.commit()
    return bundle


# ---- DELETE /auth/me (GDPR Art. 17 self-serve erasure) --------------------


# Sentinel password hash that is not a valid bcrypt output — bcrypt.checkpw
# against this string will always return False. Prevents any code path that
# ever calls verify_password from accidentally re-authenticating the user.
_DEACTIVATED_PASSWORD_SENTINEL = "!deactivated!"


def _anonymized_email(user_id: uuid.UUID) -> str:
    # RFC 2606 reserves `.invalid` for guaranteed-nonresolvable domains, so
    # the anonymized email can never be mistaken for a real address or
    # trigger a bounced welcome email if rehashed by a downstream job.
    return f"deleted+{user_id}@nextballup.invalid"


@router.delete("/me", response_model=AccountDeleteResponse)
async def delete_my_account(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    current_user: User = Depends(get_current_user),
) -> AccountDeleteResponse:
    """Anonymize this user's row and revoke every live session.

    Why anonymize rather than DELETE FROM users:
      * Video.uploaded_by and AuditLog.actor_user_id both FK the user — we
        want audit continuity (who took action X when), just without PII.
      * Team rosters need a stable identity to link `team_memberships` rows
        back to; hard-deleting would leave dangling FKs (CASCADE would also
        take out the membership history the team owns).

    What the delete guarantees:
      * Email is rewritten to a deterministic nonresolvable address.
      * Personal fields (full_name, phone, institution, avatar, player
        biometrics) are cleared.
      * Password hash is replaced with a non-bcrypt sentinel — login is
        permanently impossible.
      * session_version is bumped — every outstanding access / refresh /
        playback token fails next validation.
      * All memberships are deactivated so the user disappears from team
        rosters even though the rows remain for referential integrity.
    """
    # Gather non-PII context for the audit record before anonymization.
    pre_role = current_user.role.value

    # Deactivate memberships up-front so the user vanishes from rosters even
    # if the subsequent anonymization step fails and we roll back below.
    memberships_result = await session.execute(
        select(TeamMembership).where(TeamMembership.user_id == current_user.id)
    )
    for membership in memberships_result.scalars().all():
        membership.is_active = False
        membership.team_role = TeamRole.PLAYER

    current_user.email = _anonymized_email(current_user.id)
    current_user.full_name = "[deleted user]"
    current_user.phone = None
    current_user.institution = None
    current_user.avatar_url = None
    current_user.password_hash = _DEACTIVATED_PASSWORD_SENTINEL
    current_user.is_active = False
    # Player-specific biometrics are PII in FERPA/BIPA contexts — scrub them
    # too so the anonymized row is truly free of individual-identifying data.
    current_user.height_inches = None
    current_user.weight_lbs = None
    current_user.position = None
    current_user.graduation_year = None
    current_user.handedness = None
    current_user.biometric_consent = False
    current_user.parental_consent_on_file = False
    current_user.date_of_birth_verified = False
    await _revoke_refresh_sessions(session, user_id=current_user.id, reason="account_deleted")
    current_user.session_version += 1

    deleted_at = datetime.now(tz=UTC)
    await session.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == current_user.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(
            used_at=deleted_at,
            requested_ip=None,
            requested_user_agent=None,
            confirmed_ip=None,
        )
    )
    await session.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == current_user.id)
        .values(
            requested_ip=None,
            requested_user_agent=None,
            confirmed_ip=None,
        )
    )
    await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == current_user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(
            used_at=deleted_at,
            requested_ip=None,
            requested_user_agent=None,
            reset_ip=None,
        )
    )
    await session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == current_user.id)
        .values(
            requested_ip=None,
            requested_user_agent=None,
            reset_ip=None,
        )
    )
    await session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == current_user.id))
    await session.execute(delete(UserTotpSecret).where(UserTotpSecret.user_id == current_user.id))
    await session.execute(
        update(TeamPrivacyConsent)
        .where(TeamPrivacyConsent.recorded_by == current_user.id)
        .values(recorded_by=None)
    )
    await session.execute(
        update(BillingAccount)
        .where(BillingAccount.owner_user_id == current_user.id)
        .values(owner_user_id=None, billing_email=None)
    )
    await session.execute(
        update(CspReport)
        .where(CspReport.user_id == current_user.id)
        .values(user_id=None, reporter_ip=None, user_agent=None)
    )

    await write_audit(
        session,
        action=AuditAction.USER_ACCOUNT_DELETED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="user",
        resource_id=current_user.id,
        extra={"role": pre_role},
    )
    await session.commit()

    clear_auth_cookies(response, settings=settings)
    clear_csrf_cookie(response, settings=settings)

    return AccountDeleteResponse(deleted_at=deleted_at, user_id=current_user.id)


# ---- Email verification --------------------------------------------------


@router.post(
    "/email/verify/request",
    response_model=RequestEmailVerificationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_email_verification(
    payload: RequestEmailVerificationRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> RequestEmailVerificationResponse:
    """Mint a fresh verification token and hand it to the email provider.

    The endpoint is authenticated so the rate limiter can key by user id.
    Already-verified accounts are accepted with an idempotent 202 response —
    we still rate-limit to prevent enumeration of the verified state.
    """
    del payload  # body is intentionally empty; required for forward compatibility
    await enforce_rate_limit(
        request=request,
        settings=settings,
        scope="email_verification_request",
        subject=str(current_user.id),
        max_attempts=settings.email_verification_request_rate_attempts,
        window_seconds=settings.email_verification_request_rate_window_seconds,
    )

    if current_user.is_verified:
        # No-op for already-verified accounts; surfacing a 409 here would let
        # an attacker enumerate which addresses are verified. The response is
        # the same shape as a real issuance, with the existing verification
        # timestamp surfaced via /status if needed.
        await write_audit(
            session,
            action=AuditAction.USER_EMAIL_VERIFICATION_REQUESTED,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            resource_type="user",
            resource_id=current_user.id,
            extra={"reason": "already_verified"},
        )
        await session.commit()
        now = datetime.now(tz=UTC)
        return RequestEmailVerificationResponse(
            requested_at=now,
            expires_at=now,
            delivery=settings.email_delivery_provider,
        )

    issued = await issue_verification_token(
        session, user=current_user, request=request, settings=settings
    )
    # Best-effort delivery: we audit the request before sending, so a provider
    # failure is observable but does not break the endpoint contract.
    await write_audit(
        session,
        action=AuditAction.USER_EMAIL_VERIFICATION_REQUESTED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="email_verification_token",
        resource_id=issued.record.id,
        extra={
            "expires_at": issued.expires_at.isoformat(),
            "provider": settings.email_delivery_provider,
        },
    )
    await session.commit()
    try:
        deliver_verification_email(user=current_user, raw_token=issued.raw_token, settings=settings)
        await write_audit(
            session,
            action=AuditAction.USER_EMAIL_VERIFICATION_SENT,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            resource_type="email_verification_token",
            resource_id=issued.record.id,
            extra={"provider": settings.email_delivery_provider},
        )
        await session.commit()
    except Exception as exc:
        await write_audit(
            session,
            action=AuditAction.USER_EMAIL_VERIFICATION_REJECTED,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            resource_type="email_verification_token",
            resource_id=issued.record.id,
            extra={"reason": "delivery_failed", "provider": settings.email_delivery_provider},
        )
        await session.commit()
        raise AppError(
            "Email delivery is temporarily unavailable",
            code=ErrorCode.INTERNAL_ERROR,
            status_code=503,
        ) from exc
    return RequestEmailVerificationResponse(
        requested_at=datetime.now(tz=UTC),
        expires_at=issued.expires_at,
        delivery=settings.email_delivery_provider,
    )


@router.post(
    "/email/verify/confirm",
    response_model=ConfirmEmailVerificationResponse,
)
async def confirm_email_verification(
    payload: ConfirmEmailVerificationRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ConfirmEmailVerificationResponse:
    """Redeem a verification token. Anonymous on purpose: the user clicks a
    link in their email and may not yet be logged in on the device they're
    viewing it from. The token itself is the credential, single-use,
    short-lived, and SHA-256-bound.
    """
    confirmed, reason = await confirm_verification_token(
        session,
        raw_token=payload.token,
        request=request,
        settings=settings,
    )
    if confirmed is None:
        await write_audit(
            session,
            action=AuditAction.USER_EMAIL_VERIFICATION_REJECTED,
            request=request,
            extra={"reason": reason or "invalid"},
        )
        await session.commit()
        if reason == "expired":
            raise AppError(
                "Verification link has expired",
                code=ErrorCode.EMAIL_VERIFICATION_TOKEN_EXPIRED,
                status_code=400,
            )
        if reason == "used":
            raise AppError(
                "Verification link has already been used",
                code=ErrorCode.EMAIL_VERIFICATION_TOKEN_USED,
                status_code=409,
            )
        if reason == "already_verified":
            raise ConflictError(
                "Email is already verified",
                code=ErrorCode.EMAIL_ALREADY_VERIFIED,
            )
        # `invalid` and any other reason collapse into 400 so unknown / wrong /
        # malformed tokens look identical to the caller (no oracle).
        raise AppError(
            "Verification link is not valid",
            code=ErrorCode.EMAIL_VERIFICATION_TOKEN_INVALID,
            status_code=400,
        )

    user = await session.get(User, confirmed.user_id)
    await write_audit(
        session,
        action=AuditAction.USER_EMAIL_VERIFICATION_CONFIRMED,
        request=request,
        actor_user_id=confirmed.user_id,
        actor_email=user.email if user is not None else None,
        resource_type="user",
        resource_id=confirmed.user_id,
    )
    await session.commit()
    return ConfirmEmailVerificationResponse(
        confirmed_at=confirmed.confirmed_at,
        is_verified=True,
    )


@router.get(
    "/email/verify/status",
    response_model=EmailVerificationStatusResponse,
)
async def email_verification_status(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EmailVerificationStatusResponse:
    """Surface whether the caller has a live verification token outstanding.

    Used by the frontend to render the right UI (verify CTA vs. pending state
    vs. all-done) without polling the request endpoint.
    """
    pending_row = await session.scalar(
        select(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == current_user.id,
            EmailVerificationToken.used_at.is_(None),
            EmailVerificationToken.expires_at > datetime.now(tz=UTC),
        )
        .order_by(EmailVerificationToken.created_at.desc())
    )
    last_request = await session.scalar(
        select(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == current_user.id)
        .order_by(EmailVerificationToken.created_at.desc())
    )
    last_confirmed = await session.scalar(
        select(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == current_user.id,
            EmailVerificationToken.used_at.is_not(None),
            EmailVerificationToken.confirmed_ip.is_not(None),
        )
        .order_by(EmailVerificationToken.used_at.desc())
    )
    return EmailVerificationStatusResponse(
        is_verified=current_user.is_verified,
        pending_request=pending_row is not None,
        last_requested_at=last_request.created_at if last_request else None,
        last_confirmed_at=last_confirmed.used_at if last_confirmed else None,
    )
