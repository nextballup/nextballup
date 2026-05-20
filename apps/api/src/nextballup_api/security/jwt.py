from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

from nextballup_core.enums import UserRole
from nextballup_core.errors import AuthenticationError
from nextballup_core.settings import Settings, get_settings

TokenType = Literal["access", "refresh", "playback"]
_MAX_JWT_BYTES = 8192
_B64URL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


class _JwtDecodeError(Exception):
    """Internal marker for token parsing and verification failures."""


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not value or any(char not in _B64URL_CHARS for char in value):
        raise _JwtDecodeError("Malformed token segment")
    padding_len = (-len(value)) % 4
    try:
        return base64.urlsafe_b64decode(value + ("=" * padding_len))
    except (binascii.Error, ValueError) as exc:
        raise _JwtDecodeError("Malformed token segment") from exc


def _json_b64url_encode(value: dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _b64url_encode(raw)


def _json_b64url_decode(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(_b64url_decode(value))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _JwtDecodeError("Malformed token JSON") from exc
    if not isinstance(decoded, dict):
        raise _JwtDecodeError("Malformed token JSON")
    return decoded


def _load_private_key(settings: Settings) -> RSAPrivateKey:
    key = serialization.load_pem_private_key(
        settings.load_jwt_private_key().encode("utf-8"),
        password=None,
    )
    if not isinstance(key, RSAPrivateKey):
        raise RuntimeError("JWT private key must be an RSA private key")
    return key


def _load_public_key(settings: Settings) -> RSAPublicKey:
    key = serialization.load_pem_public_key(settings.load_jwt_public_key().encode("utf-8"))
    if not isinstance(key, RSAPublicKey):
        raise RuntimeError("JWT public key must be an RSA public key")
    return key


def _encode_rs256(claims: dict[str, Any], *, settings: Settings) -> str:
    header = {"alg": settings.jwt_algorithm, "typ": "JWT"}
    signing_input = f"{_json_b64url_encode(header)}.{_json_b64url_encode(claims)}"
    signature = _load_private_key(settings).sign(
        signing_input.encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return f"{signing_input}.{_b64url_encode(signature)}"


def _validate_registered_claims(
    claims: dict[str, Any],
    *,
    settings: Settings,
    audience: str | None,
) -> None:
    if claims.get("iss") != settings.jwt_issuer:
        raise _JwtDecodeError("Invalid token issuer")
    exp = claims.get("exp")
    if not isinstance(exp, int):
        raise _JwtDecodeError("Malformed token expiry")
    if exp <= int(_now_utc().timestamp()):
        raise _JwtDecodeError("Expired token")

    if audience is None:
        return

    token_audience = claims.get("aud")
    if isinstance(token_audience, str):
        audience_matches = token_audience == audience
    elif isinstance(token_audience, list) and all(isinstance(item, str) for item in token_audience):
        audience_matches = audience in token_audience
    else:
        audience_matches = False
    if not audience_matches:
        raise _JwtDecodeError("Invalid token audience")


def _decode_rs256(
    token: str,
    *,
    settings: Settings,
    audience: str | None,
) -> dict[str, Any]:
    if len(token.encode("utf-8")) > _MAX_JWT_BYTES:
        raise _JwtDecodeError("Token too large")

    parts = token.split(".")
    if len(parts) != 3:
        raise _JwtDecodeError("Malformed token")
    header_segment, payload_segment, signature_segment = parts
    header = _json_b64url_decode(header_segment)
    claims = _json_b64url_decode(payload_segment)

    if header.get("alg") != settings.jwt_algorithm:
        raise _JwtDecodeError("Unexpected token algorithm")
    if header.get("typ", "JWT") != "JWT":
        raise _JwtDecodeError("Unexpected token type")

    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = _b64url_decode(signature_segment)
    try:
        _load_public_key(settings).verify(
            signature,
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature as exc:
        raise _JwtDecodeError("Invalid token signature") from exc

    _validate_registered_claims(claims, settings=settings, audience=audience)
    return claims


def _build_claims(
    *,
    subject: uuid.UUID,
    role: UserRole,
    team_ids: list[uuid.UUID],
    session_version: int,
    token_type: TokenType,
    expires_in: timedelta,
    settings: Settings,
    jti: str | None = None,
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
        "jti": jti or str(uuid.uuid4()),
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
    return _encode_rs256(claims, settings=settings)


def create_refresh_token(
    *,
    subject: uuid.UUID,
    role: UserRole,
    session_version: int,
    team_ids: list[uuid.UUID] | None = None,
    settings: Settings | None = None,
    jti: str | None = None,
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
        jti=jti,
    )
    return _encode_rs256(claims, settings=settings)


def create_playback_token(
    *,
    subject: uuid.UUID,
    role: UserRole,
    session_version: int,
    video_id: uuid.UUID,
    team_id: uuid.UUID,
    expires_in: timedelta | None = None,
    settings: Settings | None = None,
) -> tuple[str, datetime]:
    """Mint a short-lived JWT scoped to a single video for the given user.

    `sv` (session_version) + `role` are included so the verify endpoint can
    cross-check them against the live user record; a subsequent logout bumps
    session_version and invalidates any still-live playback token without
    having to revoke presigned URLs individually.
    """
    settings = settings or get_settings()
    ttl = expires_in or timedelta(seconds=settings.playback_token_expire_seconds)
    now = _now_utc()
    expires_at = now + ttl
    claims: dict[str, Any] = {
        "sub": str(subject),
        "role": role.value,
        "sv": session_version,
        "type": "playback",
        "aud": settings.playback_token_audience,
        "vid": str(video_id),
        "tid": str(team_id),
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return _encode_rs256(claims, settings=settings), expires_at


def decode_token(
    token: str,
    *,
    expected_type: TokenType = "access",
    settings: Settings | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        decoded = _decode_rs256(token, settings=settings, audience=audience)
    except _JwtDecodeError as exc:
        raise AuthenticationError("Invalid or expired token") from exc

    if decoded.get("type") != expected_type:
        raise AuthenticationError("Wrong token type")
    if not isinstance(decoded.get("sub"), str):
        raise AuthenticationError("Malformed token subject")
    if not isinstance(decoded.get("jti"), str):
        raise AuthenticationError("Malformed token id")
    if not isinstance(decoded.get("iat"), int):
        raise AuthenticationError("Malformed token issue time")
    # All token types now carry `sv`; playback adds vid/tid/role for the
    # verify endpoint to cross-check against the live user + membership row.
    if not isinstance(decoded.get("sv"), int):
        raise AuthenticationError("Malformed session version")
    if expected_type == "playback":
        if not isinstance(decoded.get("vid"), str):
            raise AuthenticationError("Malformed playback subject")
        if not isinstance(decoded.get("tid"), str):
            raise AuthenticationError("Malformed playback tenant")
        if not isinstance(decoded.get("role"), str):
            raise AuthenticationError("Malformed playback role")
    return decoded
