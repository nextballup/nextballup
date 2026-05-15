from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api.audit import write_audit
from nextballup_api.deps import get_app_settings, get_db
from nextballup_api.security.rate_limit import enforce_rate_limit
from nextballup_core.constants import AuditAction
from nextballup_core.errors import TooManyRequestsError
from nextballup_core.schemas.marketing import (
    PilotInterestRequest,
    PilotInterestResponse,
)
from nextballup_core.settings import Settings

router = APIRouter(tags=["marketing"])
logger = logging.getLogger(__name__)

_PILOT_RATE_LIMIT_ATTEMPTS = 5
_PILOT_RATE_LIMIT_WINDOW_SECONDS = 60 * 60


@router.post(
    "/pilot-interest",
    response_model=PilotInterestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_pilot_interest(
    payload: PilotInterestRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> PilotInterestResponse:
    """Unauthenticated pilot-interest submission from the public marketing
    site. Rate-limited per IP, audit-logged, and intentionally returns the
    same neutral payload on success regardless of input — the response must
    not echo the submission, leak whether triage email succeeded, or
    confirm/deny prior submissions from the same IP.
    """
    try:
        await enforce_rate_limit(
            request=request,
            settings=settings,
            scope="pilot_interest",
            subject="submit",
            max_attempts=_PILOT_RATE_LIMIT_ATTEMPTS,
            window_seconds=_PILOT_RATE_LIMIT_WINDOW_SECONDS,
        )
    except TooManyRequestsError:
        # Record the rejection so abusive sources are still visible in audit
        # even when the limiter prevented the write side-effect.
        await write_audit(
            session,
            action=AuditAction.PILOT_INTEREST_REJECTED,
            request=request,
            actor_email=None,
            extra={"reason": "rate_limited"},
        )
        await session.commit()
        raise

    # The audit row carries only what triage needs. Full message text is
    # stored so the inbound triage email can be reconstructed without a
    # second source of truth; the audit log itself is already access-gated
    # to admins.
    await write_audit(
        session,
        action=AuditAction.PILOT_INTEREST_RECEIVED,
        request=request,
        actor_email=payload.email,
        resource_type="pilot_interest",
        extra={
            "full_name": payload.full_name,
            "role": payload.role,
            "organization": payload.organization,
            "message": payload.message,
        },
    )
    await session.commit()
    return PilotInterestResponse()
