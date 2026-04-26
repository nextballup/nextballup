"""Email verification token issuance + confirmation.

Threat model:
  * Token is a 32-byte URL-safe random string (~256 bits of entropy).
  * Only its SHA-256 hash is persisted; the raw token is delivered via the
    email provider and never stored. A DB read therefore does not leak any
    valid token.
  * Each token is single-use (`used_at`) and short-lived (TTL from
    settings). Replay → 401 with a stable error code.
  * Request endpoint is rate-limited per user (and per IP via the generic
    rate limiter) to prevent inbox abuse / enumeration.

Design choice: the request endpoint is authenticated. Anonymous resend would
make the platform a free email-spamming relay; an authenticated request lets
the rate limiter key by user id directly.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api.email_delivery import EmailMessage, get_email_provider
from nextballup_api.request_meta import client_ip
from nextballup_api.tenant import set_user_context
from nextballup_core.settings import Settings
from nextballup_db.models.email_verification import EmailVerificationToken
from nextballup_db.models.user import User


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _generate_token() -> str:
    # 32 bytes → 43-char URL-safe string. Plenty of entropy and short enough
    # for typical email clients to render as a single link.
    return secrets.token_urlsafe(32)


def _truncated_user_agent(request: Request) -> str | None:
    user_agent = request.headers.get("user-agent")
    if not user_agent:
        return None
    return user_agent[:500]


@dataclass(frozen=True)
class IssuedVerification:
    raw_token: str
    token_hash: str
    expires_at: datetime
    record: EmailVerificationToken


async def issue_verification_token(
    session: AsyncSession,
    *,
    user: User,
    request: Request,
    settings: Settings,
) -> IssuedVerification:
    """Mint and persist a single-use verification token for `user`.

    Previously-issued *unused* tokens for this user are invalidated by
    setting their `used_at` to now with the marker reason "superseded".
    This keeps the active-token set small and makes the most-recent link
    the only valid one — a UX choice that matches typical resend flows.
    """
    raw_token = _generate_token()
    token_hash = _hash_token(raw_token)
    now = datetime.now(tz=UTC)
    expires_at = now + timedelta(minutes=settings.email_verification_token_ttl_minutes)
    await set_user_context(session, user.id)

    # Invalidate any prior unused tokens so the freshest link wins. We mark
    # them used (rather than deleting) so the audit trail shows the
    # supersession.
    await session.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    record = EmailVerificationToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        requested_ip=client_ip(request, settings=settings),
        requested_user_agent=_truncated_user_agent(request),
    )
    session.add(record)
    await session.flush()
    return IssuedVerification(
        raw_token=raw_token,
        token_hash=token_hash,
        expires_at=expires_at,
        record=record,
    )


def deliver_verification_email(
    *,
    user: User,
    raw_token: str,
    settings: Settings,
) -> None:
    """Hand the freshly-issued token to the configured delivery provider.

    The body is intentionally a plain-text link — the platform repo does not
    own the production email template (counsel/design/anti-phishing review
    happens at the provider). The dev `logging` provider records the link so
    local development can confirm without a real inbox.
    """
    provider = get_email_provider(settings)
    link = settings.email_verification_link(raw_token)
    body = (
        f"Hi {user.full_name},\n\n"
        f"Confirm your NextBallUp email by visiting:\n  {link}\n\n"
        f"This link expires in {settings.email_verification_token_ttl_minutes} minutes "
        f"and can only be used once.\n\n"
        f"If you did not request verification, you can ignore this email.\n"
    )
    provider.send(
        EmailMessage(
            to_address=user.email,
            subject="Verify your NextBallUp email",
            body_plaintext=body,
            link_url=link,
            template_id="email.verification.v1",
            metadata={"user_id": str(user.id), "from": settings.email_verification_from_address},
        )
    )


@dataclass(frozen=True)
class ConfirmedVerification:
    user_id: uuid.UUID
    confirmed_at: datetime


async def confirm_verification_token(
    session: AsyncSession,
    *,
    raw_token: str,
    request: Request,
    settings: Settings,
) -> tuple[ConfirmedVerification | None, str | None]:
    """Look up + redeem a token. Returns (success, reason-on-failure)."""
    if not raw_token or len(raw_token) > 256:
        return None, "invalid"
    token_hash = _hash_token(raw_token)
    await session.execute(
        text("SELECT set_config('app.current_email_verification_token_hash', :v, true)").bindparams(
            v=token_hash
        )
    )
    token = await session.scalar(
        select(EmailVerificationToken)
        .where(EmailVerificationToken.token_hash == token_hash)
        .with_for_update()
    )
    if token is None:
        return None, "invalid"
    now = datetime.now(tz=UTC)
    if token.used_at is not None:
        return None, "used"
    if token.expires_at <= now:
        return None, "expired"
    user = await session.get(User, token.user_id)
    if user is None or not user.is_active:
        return None, "invalid"
    if user.is_verified:
        # Idempotent: stamp the token as used so the same link cannot be
        # replayed against a now-verified account, but report "already
        # verified" up the stack so the audit log is accurate.
        token.used_at = now
        token.confirmed_ip = client_ip(request, settings=settings)
        return None, "already_verified"
    user.is_verified = True
    token.used_at = now
    token.confirmed_ip = client_ip(request, settings=settings)
    return ConfirmedVerification(user_id=user.id, confirmed_at=now), None
