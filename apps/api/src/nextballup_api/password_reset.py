from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api.email_delivery import EmailMessage, get_email_provider
from nextballup_api.request_meta import client_ip
from nextballup_api.tenant import set_user_context
from nextballup_core.settings import Settings
from nextballup_db.models.password_reset import PasswordResetToken
from nextballup_db.models.user import User


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _truncated_user_agent(request: Request) -> str | None:
    user_agent = request.headers.get("user-agent")
    if not user_agent:
        return None
    return user_agent[:500]


@dataclass(frozen=True)
class IssuedPasswordReset:
    raw_token: str
    token_hash: str
    expires_at: datetime
    record: PasswordResetToken


async def issue_password_reset_token(
    session: AsyncSession,
    *,
    user: User,
    request: Request,
    settings: Settings,
) -> IssuedPasswordReset:
    locked_user = await session.scalar(select(User).where(User.id == user.id).with_for_update())
    if locked_user is None or not locked_user.is_active:
        raise ValueError("Cannot issue a password reset token for an inactive user")
    raw_token = _generate_token()
    token_hash = _hash_token(raw_token)
    now = datetime.now(tz=UTC)
    expires_at = now + timedelta(minutes=settings.password_reset_token_ttl_minutes)
    await set_user_context(session, locked_user.id)
    await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == locked_user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    record = PasswordResetToken(
        user_id=locked_user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        requested_ip=client_ip(request, settings=settings),
        requested_user_agent=_truncated_user_agent(request),
    )
    session.add(record)
    await session.flush()
    return IssuedPasswordReset(
        raw_token=raw_token,
        token_hash=token_hash,
        expires_at=expires_at,
        record=record,
    )


def deliver_password_reset_email(
    *,
    user: User,
    raw_token: str,
    settings: Settings,
) -> None:
    provider = get_email_provider(settings)
    link = settings.password_reset_link(raw_token)
    body = (
        f"Hi {user.full_name},\n\n"
        f"Reset your NextBallUp password by visiting:\n  {link}\n\n"
        f"This link expires in {settings.password_reset_token_ttl_minutes} minutes "
        f"and can only be used once.\n\n"
        f"If you did not request a password reset, you can ignore this email.\n"
    )
    provider.send(
        EmailMessage(
            to_address=user.email,
            subject="Reset your NextBallUp password",
            body_plaintext=body,
            link_url=link,
            template_id="auth.password_reset.v1",
            metadata={"user_id": str(user.id)},
        )
    )


@dataclass(frozen=True)
class ConsumedPasswordReset:
    user: User
    token: PasswordResetToken
    reset_at: datetime


async def consume_password_reset_token(
    session: AsyncSession,
    *,
    raw_token: str,
    request: Request,
    settings: Settings,
) -> tuple[ConsumedPasswordReset | None, str | None]:
    if not raw_token or len(raw_token) > 256:
        return None, "invalid"
    token_hash = _hash_token(raw_token)
    await session.execute(
        text("SELECT set_config('app.current_password_reset_token_hash', :v, true)").bindparams(
            v=token_hash
        )
    )
    token_user_id = await session.scalar(
        select(PasswordResetToken.user_id).where(PasswordResetToken.token_hash == token_hash)
    )
    if token_user_id is None:
        return None, "invalid"
    user = await session.scalar(select(User).where(User.id == token_user_id).with_for_update())
    token = await session.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.user_id == token_user_id,
        )
        .with_for_update()
    )
    if token is None:
        return None, "invalid"
    now = datetime.now(tz=UTC)
    if token.used_at is not None:
        return None, "used"
    if token.expires_at <= now:
        return None, "expired"
    if user is None or not user.is_active:
        token.used_at = now
        return None, "invalid"
    await set_user_context(session, user.id)
    token.used_at = now
    token.reset_ip = client_ip(request, settings=settings)
    return ConsumedPasswordReset(user=user, token=token, reset_at=now), None
