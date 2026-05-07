"""Tier-aware CV artifact selection.

Coverage:
    * `_active_artifact_for_stage` filters by `min_plan_tier <= plan_tier`.
    * Highest-tier matching artifact wins under tied creation time.
    * Newer artifact wins under tied tier.
    * Fail-closed when no artifact is entitled to the caller's plan.
    * Free-tier (tier=0) callers can still pick a tier-0 artifact (legacy
      backward compatibility).
"""

from __future__ import annotations

import uuid

import pytest
from nextballup_worker.runtime.cv_pipeline import _active_artifact_for_stage
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.enums import ModelArtifactStatus, ProcessingJobStage
from nextballup_db.models.cv import CVModelArtifact


async def _seed_artifact(
    session: AsyncSession,
    *,
    stage: ProcessingJobStage,
    tier: int,
    version: str,
    status: ModelArtifactStatus = ModelArtifactStatus.ACTIVE,
    commercial: bool = True,
) -> CVModelArtifact:
    row = CVModelArtifact(
        stage=stage,
        status=status,
        artifact_uri=f"s3://fake/{version}",
        model_version=version,
        license="apache-2.0",
        commercial_use_allowed=commercial,
        min_plan_tier=tier,
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio(loop_scope="session")
async def test_higher_tier_artifact_wins_for_pro_caller(
    db_session: AsyncSession,
) -> None:
    free = await _seed_artifact(
        db_session, stage=ProcessingJobStage.DETECTION, tier=0, version="v1.free"
    )
    pro = await _seed_artifact(
        db_session, stage=ProcessingJobStage.DETECTION, tier=20, version="v1.pro"
    )
    chosen = await _active_artifact_for_stage(
        db_session, ProcessingJobStage.DETECTION, plan_tier=20
    )
    assert chosen is not None
    assert chosen.id == pro.id, f"expected pro, got {chosen.model_version}"
    # Sanity: free is still selectable for free callers.
    chosen_free = await _active_artifact_for_stage(
        db_session, ProcessingJobStage.DETECTION, plan_tier=0
    )
    assert chosen_free is not None
    assert chosen_free.id == free.id


@pytest.mark.asyncio(loop_scope="session")
async def test_caller_below_min_tier_is_rejected(db_session: AsyncSession) -> None:
    await _seed_artifact(
        db_session,
        stage=ProcessingJobStage.TRACKING,
        tier=20,
        version="v1.tracker.pro",
    )
    chosen = await _active_artifact_for_stage(db_session, ProcessingJobStage.TRACKING, plan_tier=10)
    assert chosen is None


@pytest.mark.asyncio(loop_scope="session")
async def test_only_active_commercial_artifacts_are_selectable(
    db_session: AsyncSession,
) -> None:
    await _seed_artifact(
        db_session,
        stage=ProcessingJobStage.EVENTS,
        tier=0,
        version="v1.candidate",
        status=ModelArtifactStatus.CANDIDATE,
    )
    await _seed_artifact(
        db_session,
        stage=ProcessingJobStage.EVENTS,
        tier=0,
        version="v1.noncomm",
        commercial=False,
    )
    chosen = await _active_artifact_for_stage(db_session, ProcessingJobStage.EVENTS, plan_tier=20)
    assert chosen is None


@pytest.mark.asyncio(loop_scope="session")
async def test_restricted_alpha_detector_cannot_be_promoted_to_commercial_artifact(
    db_session: AsyncSession,
) -> None:
    await _seed_artifact(
        db_session,
        stage=ProcessingJobStage.DETECTION,
        tier=0,
        version=f"basketball.detect.ebard_alpha_demo-{uuid.uuid4().hex[:6]}",
        commercial=False,
        status=ModelArtifactStatus.ACTIVE,
    )

    chosen = await _active_artifact_for_stage(
        db_session,
        ProcessingJobStage.DETECTION,
        plan_tier=20,
    )

    assert chosen is None


@pytest.mark.asyncio(loop_scope="session")
async def test_newer_artifact_wins_for_same_tier(db_session: AsyncSession) -> None:
    older = await _seed_artifact(
        db_session,
        stage=ProcessingJobStage.METRICS,
        tier=10,
        version=f"v1.older-{uuid.uuid4().hex[:6]}",
    )
    # Force the older row's created_at backwards so the ORDER BY is decisive.
    from datetime import UTC, datetime, timedelta

    older.created_at = datetime.now(tz=UTC) - timedelta(days=30)
    newer = await _seed_artifact(
        db_session,
        stage=ProcessingJobStage.METRICS,
        tier=10,
        version=f"v1.newer-{uuid.uuid4().hex[:6]}",
    )
    await db_session.flush()

    chosen = await _active_artifact_for_stage(db_session, ProcessingJobStage.METRICS, plan_tier=10)
    assert chosen is not None
    assert chosen.id == newer.id
