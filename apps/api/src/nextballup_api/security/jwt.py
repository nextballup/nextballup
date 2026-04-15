from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from jose import JWTError, jwt

from nextballup_core.enums import UserRole
from nextballup_core.errors import AuthenticationError
from nextballup_core.settings import Settings, get_settings

TokenType = Literal["access", "refresh", "playback"]


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _build_claims(
    *,
    subject: uuid.UUID,
    role: UserRole,
    team_ids: list[uuid.UUID],
    session_version: int,
    token_type: TokenType,
    expires_in: timedelta,
    settings: Settings,
) -> dict[str, Any]:
    now = _now_utc()
    return {
        "sub": str(subject),
        "role": role.value,
        "team_ids": [str(t) for t in team_ids],
        "sv": session_version,
        "type": token_type,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
        "jti": str(uuid.uuid4()),
    }


def create_access_token(
    *,
    subject: uuid.UUID,
    role: UserRole,
    session_version: int,
    team_ids: list[uuid.UUID] | None = None,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    claims = _build_claims(
        subject=subject,
        role=role,
        team_ids=team_ids or [],
        session_version=session_version,
        token_type="access",
        expires_in=timedelta(minutes=settings.access_token_expire_minutes),
        settings=settings,
    )
    encoded: str = jwt.encode(
        claims,
        settings.load_jwt_private_key(),
        algorithm=settings.jwt_algorithm,
    )
    return encoded


def create_refresh_token(
    *,
    subject: uuid.UUID,
    role: UserRole,
    session_version: int,
    team_ids: list[uuid.UUID] | None = None,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    claims = _build_claims(
        subject=subject,
        role=role,
        team_ids=team_ids or [],
        session_version=session_version,
        token_type="refresh",
        expires_in=timedelta(days=settings.refresh_token_expire_days),
        settings=settings,
    )
    encoded: str = jwt.encode(
        claims,
        settings.load_jwt_private_key(),
        algorithm=settings.jwt_algorithm,
    )
    return encoded


def create_playback_token(
    *,
    subject: uuid.UUID,
    video_id: uuid.UUID,
    team_id: uuid.UUID,
    expires_in: timedelta | None = None,
    settings: Settings | None = None,
) -> tuple[str, datetime]:
    """Mint a short-lived JWT scoped to a single video for the given user.

    The presigned storage URL is the actual access boundary; this token is the
    forward-compat hook for a future `/videos/{id}/playback/verify` endpoint
    that will let session-aware revocation gate playback. We emit it now so
    clients can store it next to the URL and the API contract stays stable.
    """
    settings = settings or get_settings()
    ttl = expires_in or timedelta(seconds=settings.playback_token_expire_seconds)
    now = _now_utc()
    expires_at = now + ttl
    claims: dict[str, Any] = {
        "sub": str(subject),
        "type": "playback",
        "aud": settings.playback_token_audience,
        "vid": str(video_id),
        "tid": str(team_id),
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    encoded: str = jwt.encode(
        claims,
        settings.load_jwt_private_key(),
        algorithm=settings.jwt_algorithm,
    )
    return encoded, expires_at


def decode_token(
    token: str,
    *,
    expected_type: TokenType = "access",
    settings: Settings | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    decode_kwargs: dict[str, Any] = {
        "algorithms": [settings.jwt_algorithm],
        "issuer": settings.jwt_issuer,
    }
    if audience is not None:
        decode_kwargs["audience"] = audience
    try:
        decoded: dict[str, Any] = jwt.decode(
            token,
            settings.load_jwt_public_key(),
            **decode_kwargs,
        )
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired token") from exc

    if decoded.get("type") != expected_type:
        raise AuthenticationError("Wrong token type")
    if not isinstance(decoded.get("sub"), str):
        raise AuthenticationError("Malformed token subject")
    if not isinstance(decoded.get("jti"), str):
        raise AuthenticationError("Malformed token id")
    if not isinstance(decoded.get("iat"), int):
        raise AuthenticationError("Malformed token issue time")
    # session_version is only present on access/refresh tokens; playback
    # tokens carry vid/tid instead.
    if expected_type in ("access", "refresh") and not isinstance(decoded.get("sv"), int):
        raise AuthenticationError("Malformed session version")
    if expected_type == "playback":
        if not isinstance(decoded.get("vid"), str):
            raise AuthenticationError("Malformed playback subject")
        if not isinstance(decoded.get("tid"), str):
            raise AuthenticationError("Malformed playback tenant")
    return decoded
