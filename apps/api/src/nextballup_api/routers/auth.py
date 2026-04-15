from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from nextballup_api.audit import write_audit
from nextballup_api.deps import get_app_settings, get_current_user, get_db
from nextballup_api.security.cookies import clear_auth_cookies, set_auth_cookies
from nextballup_api.security.jwt import create_access_token, create_refresh_token, decode_token
from nextballup_api.security.passwords import hash_password, verify_password
from nextballup_api.security.rate_limit import enforce_auth_rate_limit
from nextballup_api.tenant import set_user_context, set_user_role_context
from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.errors import (
    AuthenticationError,
    ConflictError,
    InvalidCredentialsError,
)
from nextballup_core.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    TeamMembershipSummary,
    UserPublic,
)
from nextballup_core.settings import Settings
from nextballup_db.models.team import TeamMembership
from nextballup_db.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_public(user: User) -> UserPublic:
    teams = [
        TeamMembershipSummary(
            id=m.team_id,
            name=m.team.name,
            role_in_team=m.team_role.value,
        )
        for m in user.team_memberships
        if m.is_active and m.team is not None
    ]
    return UserPublic(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        teams=teams,
    )


def _team_ids(user: User) -> list[uuid.UUID]:
    return [m.team_id for m in user.team_memberships if m.is_active]


def _issue_tokens(
    user: User,
    *,
    settings: Settings,
    team_ids: list[uuid.UUID] | None = None,
) -> tuple[str, str]:
    resolved_team_ids = team_ids if team_ids is not None else _team_ids(user)
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
    )
    return access_token, refresh_token


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
) -> User:
    claims = decode_token(token, expected_type="refresh", settings=settings)
    try:
        user_id = uuid.UUID(claims["sub"])
    except ValueError as exc:
        raise AuthenticationError("Malformed token subject") from exc

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
    return user


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

    access_token, refresh_token = _issue_tokens(user, settings=settings, team_ids=[])
    set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        settings=settings,
    )

    return RegisterResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        created_at=user.created_at,
        access_token=access_token,
        refresh_token=refresh_token,
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

    access_token, refresh_token = _issue_tokens(user, settings=settings)
    set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        settings=settings,
    )

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_user_public(user),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> RefreshResponse:
    token = payload.refresh_token or request.cookies.get(settings.cookie_refresh_name)
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
        user = await _user_from_refresh_token(token, session=session, settings=settings)
    except AuthenticationError as exc:
        await write_audit(
            session,
            action=AuditAction.USER_REFRESH_FAILED,
            request=request,
            extra={"reason": ErrorCode.UNAUTHENTICATED},
        )
        await session.commit()
        raise exc

    access_token, refresh_token = _issue_tokens(user, settings=settings)
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
    return RefreshResponse(access_token=access_token, refresh_token=refresh_token)


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
    current_user.session_version += 1
    await session.commit()
    clear_auth_cookies(response, settings=settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return _user_public(current_user)
