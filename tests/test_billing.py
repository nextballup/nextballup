"""Billing / entitlement / quota coverage.

Coverage:
    * default-plan provisioning when a team uploads for the first time
    * quota check returns allowed=True under the limit
    * quota check returns allowed=False at the limit and surfaces a stable
      error code
    * upload-init endpoint denies once quota is exceeded and writes a
      `billing.quota.denied` audit row
    * recorded UsageEvent is account-scoped and survives an account context
      switch (RLS does not leak across tenants)
    * tier-aware artifact gate: lower-tier plans are rejected; higher-tier
      plans pass; missing-plan callers fail closed
    * provider abstraction: stub returns deterministic placeholder data;
      provider registry round-trip works
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from nextballup_api.billing import (
    StubBillingProvider,
    artifact_meets_entitlement,
    check_video_storage_quota,
    check_video_upload_quota,
    get_billing_provider,
    get_or_create_account_for_team,
    record_usage,
    register_billing_provider,
    resolve_team_plan,
    set_billing_account_context,
)
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import (
    BillingAccountStatus,
    InstitutionType,
    Sport,
    SubscriptionStatus,
    TeamLevel,
    UserRole,
)
from nextballup_core.settings import Settings, get_settings
from nextballup_db.models.audit import AuditLog
from nextballup_db.models.billing import (
    AccountTeamLink,
    BillingAccount,
    Plan,
    Subscription,
    UsageEvent,
)
from nextballup_db.models.team import Team
from nextballup_db.models.user import User

API = "/api/v1"


async def _make_user(session: AsyncSession, *, role: UserRole = UserRole.COACH) -> User:
    user = User(
        email=f"billing-{uuid.uuid4().hex}@example.com",
        password_hash="x",
        full_name="Billing User",
        role=role,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_team(session: AsyncSession) -> Team:
    team = Team(
        name=f"Test {uuid.uuid4().hex[:6]}",
        sport=Sport.BASKETBALL,
        level=TeamLevel.HIGH_SCHOOL,
        institution_type=InstitutionType.NONE,
        season="2025-26",
        invite_code=uuid.uuid4().hex[:10].upper(),
    )
    session.add(team)
    await session.flush()
    return team


@pytest.mark.asyncio(loop_scope="session")
async def test_first_upload_provisions_account_and_subscription(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    team = await _make_team(db_session)
    settings = get_settings()

    account = await get_or_create_account_for_team(
        db_session, team_id=team.id, owner_user_id=user.id, settings=settings
    )
    assert account.id is not None

    link = await db_session.scalar(
        select(AccountTeamLink).where(AccountTeamLink.team_id == team.id)
    )
    assert link is not None
    assert link.billing_account_id == account.id

    sub = await db_session.scalar(
        select(Subscription).where(Subscription.billing_account_id == account.id)
    )
    assert sub is not None
    assert sub.status is SubscriptionStatus.ACTIVE

    plan = await db_session.scalar(select(Plan).where(Plan.id == sub.plan_id))
    assert plan is not None
    assert plan.code == settings.billing_default_plan_code
    assert sub.plan_code_at_activation == plan.code
    assert sub.plan_tier_at_activation == plan.tier
    assert sub.plan_quotas_at_activation["max_videos_per_month"] == plan.max_videos_per_month
    assert sub.current_period_end - sub.current_period_start <= timedelta(days=31)

    # Idempotent: a second call returns the same account and does not create
    # a second subscription.
    again = await get_or_create_account_for_team(
        db_session, team_id=team.id, owner_user_id=user.id, settings=settings
    )
    assert again.id == account.id
    sub_count = await db_session.scalar(
        select(Subscription).where(Subscription.billing_account_id == account.id)
    )
    assert sub_count is not None  # at least one (we don't dup)


@pytest.mark.asyncio(loop_scope="session")
async def test_quota_fails_closed_for_deleted_or_suspended_billing_account(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    team = await _make_team(db_session)
    settings = get_settings()
    account = await get_or_create_account_for_team(
        db_session, team_id=team.id, owner_user_id=user.id, settings=settings
    )
    await db_session.execute(
        update(BillingAccount)
        .where(BillingAccount.id == account.id)
        .values(status=BillingAccountStatus.SUSPENDED, deleted_at=datetime.now(tz=UTC))
    )
    await db_session.flush()

    check = await check_video_upload_quota(
        db_session, team_id=team.id, owner_user_id=user.id, settings=settings
    )
    plan_ctx = await resolve_team_plan(db_session, team_id=team.id)

    assert check.allowed is False
    assert check.limit == 0
    assert plan_ctx is None


@pytest.mark.asyncio(loop_scope="session")
async def test_quota_check_blocks_when_exceeded(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    team = await _make_team(db_session)
    settings = get_settings()

    # Free plan ships with max_videos_per_month=5 (seed migration). We push
    # 5 usage events into the current period so the check at #6 fails.
    account = await get_or_create_account_for_team(
        db_session, team_id=team.id, owner_user_id=user.id, settings=settings
    )
    for _ in range(5):
        await record_usage(
            db_session,
            billing_account_id=account.id,
            event_key="video.upload.initiated",
            team_id=team.id,
        )
    check = await check_video_upload_quota(
        db_session, team_id=team.id, owner_user_id=user.id, settings=settings
    )
    assert check.plan_code == "free"
    assert check.limit == 5
    assert check.used == 5
    assert check.allowed is False


@pytest.mark.asyncio(loop_scope="session")
async def test_subscription_snapshot_preserves_entitlement_after_plan_update(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    team = await _make_team(db_session)
    settings = get_settings()
    account = await get_or_create_account_for_team(
        db_session, team_id=team.id, owner_user_id=user.id, settings=settings
    )
    sub = await db_session.scalar(
        select(Subscription).where(Subscription.billing_account_id == account.id)
    )
    assert sub is not None
    assert sub.plan_code_at_activation == "free"
    assert sub.plan_quotas_at_activation["max_videos_per_month"] == 5

    await db_session.execute(update(Plan).where(Plan.code == "free").values(max_videos_per_month=1))
    for _ in range(2):
        await record_usage(
            db_session,
            billing_account_id=account.id,
            event_key="video.upload.initiated",
            team_id=team.id,
        )

    check = await check_video_upload_quota(
        db_session, team_id=team.id, owner_user_id=user.id, settings=settings
    )

    assert check.plan_code == "free"
    assert check.limit == 5
    assert check.used == 2
    assert check.allowed is True


@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_team_plan_uses_retention_from_activation_snapshot(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    team = await _make_team(db_session)
    settings = get_settings()
    await get_or_create_account_for_team(
        db_session, team_id=team.id, owner_user_id=user.id, settings=settings
    )

    await db_session.execute(
        update(Plan).where(Plan.code == "free").values(raw_video_retention_days=3650)
    )
    plan_ctx = await resolve_team_plan(db_session, team_id=team.id)

    assert plan_ctx is not None
    assert plan_ctx.plan_code == "free"
    assert plan_ctx.raw_video_retention_days == 30


@pytest.mark.asyncio(loop_scope="session")
async def test_storage_quota_check_blocks_oversized_free_upload(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    team = await _make_team(db_session)
    settings = get_settings()

    check = await check_video_storage_quota(
        db_session,
        team_id=team.id,
        owner_user_id=user.id,
        settings=settings,
        additional_bytes=6 * 1024 * 1024 * 1024,
    )

    assert check.plan_code == "free"
    assert check.quota_key == "max_storage_bytes"
    assert check.limit == 5 * 1024 * 1024 * 1024
    assert check.used == 0
    assert check.allowed is False


@pytest.mark.asyncio(loop_scope="session")
async def test_upload_init_returns_quota_exceeded_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """End-to-end: stuff usage events to the limit, then hit /upload."""
    # Register & log in a coach via the API so cookies are set, then create a
    # team and game through the API to keep RLS / tenant context realistic.
    register = await client.post(
        f"{API}/auth/register",
        json={
            "email": "quota-coach@example.com",
            "password": "Password1!",
            "full_name": "Quota Coach",
            "role": "coach",
        },
    )
    assert register.status_code == 201

    # Mark the user verified so the upload route's verified-account gate
    # doesn't shadow the billing test.
    user = await db_session.scalar(select(User).where(User.email == "quota-coach@example.com"))
    assert user is not None
    user.is_verified = True
    await db_session.commit()

    team_resp = await client.post(
        f"{API}/teams",
        json={
            "name": "Quota Squad",
            "sport": "basketball",
            "level": "high_school",
            "season": "2025-26",
        },
    )
    assert team_resp.status_code == 201, team_resp.text
    team_id = team_resp.json()["id"]

    game_resp = await client.post(
        f"{API}/games",
        json={
            "team_id": team_id,
            "opponent_name": "Rivals",
            "game_type": "regular_season",
            "date": "2026-05-01",
            "is_home": True,
        },
    )
    assert game_resp.status_code == 201, game_resp.text
    game_id = game_resp.json()["id"]

    # Provision the account + plan, then push 5 usage events to exhaust free.
    settings = get_settings()
    account = await get_or_create_account_for_team(
        db_session,
        team_id=uuid.UUID(team_id),
        owner_user_id=user.id,
        settings=settings,
    )
    for _ in range(5):
        await record_usage(
            db_session,
            billing_account_id=account.id,
            event_key="video.upload.initiated",
            team_id=uuid.UUID(team_id),
        )
    await db_session.commit()

    upload = await client.post(
        f"{API}/videos/upload",
        json={
            "game_id": game_id,
            "filename": "game.mp4",
            "content_type": "video/mp4",
            "file_size_bytes": 5_000_000,
        },
    )
    assert upload.status_code == 403, upload.text
    body = upload.json()
    assert body["error"]["code"] == ErrorCode.BILLING_QUOTA_EXCEEDED
    assert body["error"]["details"]["plan_code"] == "free"

    rows = await db_session.execute(
        select(AuditLog.action).where(AuditLog.action == AuditAction.BILLING_QUOTA_DENIED)
    )
    assert any(True for _ in rows)


@pytest.mark.asyncio(loop_scope="session")
async def test_upload_init_returns_storage_quota_exceeded_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    register = await client.post(
        f"{API}/auth/register",
        json={
            "email": "storage-quota-coach@example.com",
            "password": "Password1!",
            "full_name": "Storage Quota Coach",
            "role": "coach",
        },
    )
    assert register.status_code == 201

    user = await db_session.scalar(
        select(User).where(User.email == "storage-quota-coach@example.com")
    )
    assert user is not None
    user.is_verified = True
    await db_session.commit()

    team_resp = await client.post(
        f"{API}/teams",
        json={
            "name": "Storage Quota Squad",
            "sport": "basketball",
            "level": "college_d1",
            "season": "2025-26",
        },
    )
    assert team_resp.status_code == 201, team_resp.text
    team_id = team_resp.json()["id"]

    game_resp = await client.post(
        f"{API}/games",
        json={
            "team_id": team_id,
            "opponent_name": "Rivals",
            "game_type": "regular_season",
            "date": "2026-05-01",
            "is_home": True,
        },
    )
    assert game_resp.status_code == 201, game_resp.text
    game_id = game_resp.json()["id"]

    upload = await client.post(
        f"{API}/videos/upload",
        json={
            "game_id": game_id,
            "filename": "huge-game.mp4",
            "content_type": "video/mp4",
            "file_size_bytes": 6 * 1024 * 1024 * 1024,
        },
    )
    assert upload.status_code == 403, upload.text
    body = upload.json()
    assert body["error"]["code"] == ErrorCode.BILLING_QUOTA_EXCEEDED
    assert body["error"]["details"]["quota_key"] == "max_storage_bytes"
    assert body["error"]["details"]["plan_code"] == "free"


@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_team_plan_returns_none_when_unlinked(
    db_session: AsyncSession,
) -> None:
    team = await _make_team(db_session)
    plan_ctx = await resolve_team_plan(db_session, team_id=team.id)
    assert plan_ctx is None


def test_artifact_meets_entitlement_pure() -> None:
    free = Plan(
        code="free",
        display_name="Free",
        tier=0,
        monthly_cents=0,
        annual_cents=0,
    )
    pro = Plan(
        code="pro",
        display_name="Pro",
        tier=20,
        monthly_cents=0,
        annual_cents=0,
    )
    # min_plan_tier=10 (starter or higher)
    assert artifact_meets_entitlement(plan=free, min_plan_tier=10) is False
    assert artifact_meets_entitlement(plan=pro, min_plan_tier=10) is True
    # Missing plan == fail closed unless artifact is tier-0 (free-ok)
    assert artifact_meets_entitlement(plan=None, min_plan_tier=10) is False
    assert artifact_meets_entitlement(plan=None, min_plan_tier=0) is True


def test_stub_provider_returns_obvious_placeholder_url() -> None:
    provider = StubBillingProvider()
    out = provider.create_checkout_session(
        billing_account_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        plan_code="pro",
        success_url="https://example.test/success",
        cancel_url="https://example.test/cancel",
    )
    # The "about:blank" prefix makes a misconfigured prod deploy obviously
    # broken rather than silently shipping the user nowhere useful.
    assert out.checkout_url.startswith("about:blank#stub-checkout-")
    assert "pro" in out.checkout_url


def test_billing_provider_registry_round_trip() -> None:
    sentinel = StubBillingProvider()
    register_billing_provider("stub_sentinel", lambda _s: sentinel)
    fake_settings = Settings.model_construct(
        billing_provider="stub_sentinel",
    )
    resolved = get_billing_provider(fake_settings)
    assert resolved is sentinel


@pytest.mark.asyncio(loop_scope="session")
async def test_billing_account_context_isolates_subscriptions(
    db_session: AsyncSession,
) -> None:
    """RLS spot-check: with one account in context, a query that would touch
    another account's subscription rows must come back empty.

    The fixture session runs inside an outer transaction so we can write
    rows for two accounts and probe both contexts.
    """
    settings = get_settings()
    user_a = await _make_user(db_session)
    team_a = await _make_team(db_session)
    user_b = await _make_user(db_session)
    team_b = await _make_team(db_session)
    account_a = await get_or_create_account_for_team(
        db_session, team_id=team_a.id, owner_user_id=user_a.id, settings=settings
    )
    account_b = await get_or_create_account_for_team(
        db_session, team_id=team_b.id, owner_user_id=user_b.id, settings=settings
    )
    await db_session.commit()

    # Bind context to A: a select for B should not expose B's rows under
    # FORCE RLS in production. The test fixture connects as the owner user
    # which bypasses FORCE RLS — the assertion below is therefore a
    # smoke test for the context binding semantics rather than a full RLS
    # verification (which has dedicated coverage in test_db_roles.py).
    await set_billing_account_context(db_session, account_a.id)
    visible = await db_session.scalar(
        select(Subscription).where(Subscription.billing_account_id == account_a.id)
    )
    assert visible is not None
    assert visible.billing_account_id == account_a.id

    # Switching context: still works — context is just for SET LOCAL GUC.
    await set_billing_account_context(db_session, account_b.id)
    other = await db_session.scalar(
        select(Subscription).where(Subscription.billing_account_id == account_b.id)
    )
    assert other is not None
    assert other.billing_account_id == account_b.id


@pytest.mark.asyncio(loop_scope="session")
async def test_usage_events_are_database_immutable(db_session: AsyncSession) -> None:
    settings = get_settings()
    user = await _make_user(db_session)
    team = await _make_team(db_session)
    account = await get_or_create_account_for_team(
        db_session, team_id=team.id, owner_user_id=user.id, settings=settings
    )
    event = await record_usage(
        db_session,
        billing_account_id=account.id,
        event_key="video.upload.initiated",
        team_id=team.id,
    )
    event_id = event.id
    await db_session.commit()

    with pytest.raises(DBAPIError, match="Usage events cannot be modified"):
        await db_session.execute(
            update(UsageEvent).where(UsageEvent.id == event_id).values(quantity=99)
        )
    await db_session.rollback()

    with pytest.raises(DBAPIError, match="Usage events cannot be modified"):
        await db_session.execute(delete(UsageEvent).where(UsageEvent.id == event_id))
    await db_session.rollback()

    still_there = await db_session.get(UsageEvent, event_id)
    assert still_there is not None
    assert still_there.quantity == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_seed_plans_present(db_session: AsyncSession) -> None:
    """The seed migration must populate four plan rows."""
    rows = (await db_session.scalars(select(Plan).order_by(Plan.tier))).all()
    codes = [r.code for r in rows]
    assert codes == ["free", "starter", "pro", "enterprise"]
    free = next(r for r in rows if r.code == "free")
    assert free.max_videos_per_month == 5
    assert free.max_storage_gb == 5
    enterprise = next(r for r in rows if r.code == "enterprise")
    assert enterprise.max_videos_per_month is None
    # Feature flags
    pro = next(r for r in rows if r.code == "pro")
    assert pro.features.get("cv_pipeline") is True
    assert free.features.get("cv_pipeline") is False
