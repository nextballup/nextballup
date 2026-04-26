from __future__ import annotations

import logging

from fastapi import Request
from redis.asyncio import Redis

from nextballup_api.request_meta import client_ip
from nextballup_core.errors import ServiceUnavailableError, TooManyRequestsError
from nextballup_core.settings import Settings

logger = logging.getLogger(__name__)

_RATE_LIMIT_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return { current, ttl }
"""


def _rate_limit_eval_result(value: object) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise RuntimeError("Unexpected Redis rate-limit script response")
    return int(value[0]), int(value[1])


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

    Development/test can fail open for local ergonomics. Staging/production
    fail closed by default so auth/upload/team-join endpoints do not silently
    lose abuse controls during a Redis outage.
    """
    if not settings.redis_url:
        if settings.should_rate_limit_fail_closed():
            raise ServiceUnavailableError("Rate limiting is not configured")
        return

    peer_ip = client_ip(request, settings=settings) or "unknown"
    subject_key = subject.strip().lower()
    key = f"rate_limit:{scope}:{peer_ip}:{subject_key}"
    client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    try:
        script = client.register_script(_RATE_LIMIT_LUA)
        count, retry_after = _rate_limit_eval_result(
            await script(keys=[key], args=[window_seconds])
        )
        if count > max_attempts:
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
    except Exception as exc:
        logger.warning("Rate limiting unavailable", exc_info=True)
        if settings.should_rate_limit_fail_closed():
            raise ServiceUnavailableError("Rate limiting is unavailable") from exc
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

    Production inherits the generic fail-closed policy when Redis is missing or
    unavailable.
    """
    await enforce_rate_limit(
        request=request,
        settings=settings,
        scope=scope,
        subject=subject,
        max_attempts=settings.auth_rate_limit_attempts,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )
