from __future__ import annotations

import asyncio

import boto3
from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api import __version__
from nextballup_api.deps import get_app_settings, get_db
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
