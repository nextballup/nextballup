from __future__ import annotations

import asyncio
import secrets

import boto3
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api import __version__
from nextballup_api.audit import write_audit
from nextballup_api.deps import get_app_settings, get_db
from nextballup_core.constants import AuditAction
from nextballup_core.observability import render_metrics
from nextballup_core.schemas.health import (
    HealthResponse,
    LivenessResponse,
    ReadinessResponse,
)
from nextballup_core.settings import Settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/health/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    return LivenessResponse(status="alive")


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ReadinessResponse:
    database = "ok"
    redis = "not_configured"
    storage = "not_configured"

    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        database = "error"

    if settings.redis_url:
        redis = await _check_redis(settings.redis_url)
    if _storage_configured(settings):
        storage = await _check_storage(settings)

    statuses = (database, redis, storage)
    if any(dep in {"error", "timeout"} for dep in statuses):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="not_ready",
            database=database,
            redis=redis,
            storage=storage,
        )
    return ReadinessResponse(status="ready", database=database, redis=redis, storage=storage)


@router.get("/_metrics")
async def metrics(
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    if not settings.observability_metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    expected = settings.observability_metrics_token or ""
    presented = request.headers.get("X-Metrics-Token", "")
    if not expected or not secrets.compare_digest(presented, expected):
        await write_audit(
            session,
            action=AuditAction.METRICS_SCRAPE_REJECTED,
            request=request,
            resource_type="metrics",
            extra={
                "reason": "token_not_configured" if not expected else "invalid_token",
                "has_presented_token": bool(presented),
            },
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    return Response(
        content=render_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


async def _check_redis(redis_url: str) -> str:
    client = Redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    try:
        await asyncio.wait_for(client.ping(), timeout=1.0)
    except TimeoutError:
        return "timeout"
    except Exception:
        return "error"
    finally:
        await client.aclose()
    return "ok"


def _storage_configured(settings: Settings) -> bool:
    return all(
        (
            settings.s3_endpoint_url,
            settings.s3_access_key,
            settings.s3_secret_key,
            settings.s3_bucket_raw,
        )
    )


async def _check_storage(settings: Settings) -> str:
    try:
        await asyncio.wait_for(asyncio.to_thread(_head_bucket, settings), timeout=2.0)
    except TimeoutError:
        return "timeout"
    except Exception:
        return "error"
    return "ok"


def _head_bucket(settings: Settings) -> None:
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )
    client.head_bucket(Bucket=settings.s3_bucket_raw)
