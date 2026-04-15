from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from nextballup_api.security.jwt import decode_token
from nextballup_api.tenant import set_user_context, set_user_role_context
from nextballup_core.errors import AuthenticationError
from nextballup_core.settings import Settings, get_settings
from nextballup_db.engine import get_session
from nextballup_db.models.team import TeamMembership
from nextballup_db.models.user import User

# Re-exported so routers Depend on a single import surface and tests have a
# stable target for dependency_overrides.
get_db = get_session


def get_app_settings() -> Settings:
    return get_settings()


def _extract_token(request: Request, settings: Settings) -> str:
    cookie = request.cookies.get(settings.cookie_access_name)
    if cookie:
        return cookie
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    raise AuthenticationError("Missing authentication credentials")


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> User:
    token = _extract_token(request, settings)
    claims = decode_token(token, expected_type="access", settings=settings)
    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Malformed token subject") from exc
    request.state.auth_claims = claims
    await set_user_context(session, user_id)
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User is no longer active")
    if claims["sv"] != user.session_version:
        raise AuthenticationError("Session has been invalidated")
    await set_user_role_context(session, user.role)

    # Chain selectin loaders so /auth/me — and any handler that calls
    # _user_public — can serialize membership.team.name without triggering a
    # lazy load outside the async greenlet context (sync I/O on access raises
    # MissingGreenlet under SQLAlchemy 2.0 async).
    result = await session.execute(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.team_memberships).selectinload(TeamMembership.team))
    )
    user = result.scalar_one_or_none()
    if user is None:  # pragma: no cover - guarded by the earlier point lookup
        raise AuthenticationError("User is no longer active")
    return user
