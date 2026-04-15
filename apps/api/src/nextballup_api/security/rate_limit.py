from __future__ import annotations

import logging

from fastapi import Request
from redis.asyncio import Redis

from nextballup_api.request_meta import client_ip
from nextballup_core.errors import TooManyRequestsError
from nextballup_core.settings import Settings

logger = logging.getLogger(__name__)


async def enforce_rate_limit(
    *,
    request: Request,
    settings: Settings,
    scope: str,
    subject: str,
    max_attempts: int,
    window_seconds: int,
) -> None:
    """Generic Redis-backed rate limiter.

    This fails open if Redis is unavailable so the API remains functional during
    a cache outage; readiness still reports the dependency health separately.
    """
    if not settings.redis_url:
        return

    peer_ip = client_ip(request, settings=settings) or "unknown"
    subject_key = subject.strip().lower()
    key = f"rate_limit:{scope}:{peer_ip}:{subject_key}"
    client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_seconds)
        if count > max_attempts:
            retry_after = await client.ttl(key)
            raise TooManyRequestsError(
                "Too many attempts. Please try again later.",
                details={
                    "retry_after_seconds": max(retry_after, 1),
                    "limit": max_attempts,
                    "window_seconds": window_seconds,
                },
            )
    except TooManyRequestsError:
        raise
    except Exception:
        logger.warning("Rate limiting unavailable", exc_info=True)
    finally:
        await client.aclose()


async def enforce_auth_rate_limit(
    *,
    request: Request,
    settings: Settings,
    scope: str,
    subject: str,
) -> None:
    """Rate-limit auth attempts by scope + subject + client IP when Redis is configured.

    This fails open if Redis is unavailable so auth remains functional during a
    transient cache outage. The readiness endpoint still reports Redis health
    separately for operators.
    """
    await enforce_rate_limit(
        request=request,
        settings=settings,
        scope=scope,
        subject=subject,
        max_attempts=settings.auth_rate_limit_attempts,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )
