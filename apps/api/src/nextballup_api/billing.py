"""Billing / entitlement / quota helpers.

Design goals:
  * **Tenant isolation preserved.** The team-scoped RLS contract is
    untouched. Billing data lives behind its own GUC
    (`app.current_billing_account_id`) so a request cannot read another
    tenant's subscription rows even under FORCE RLS.
  * **Schema-driven quotas.** Numeric quotas live as columns on `plans` so
    SQL aggregations (videos this period, storage this month) can enforce
    them directly. Richer capability flags live in the `features` JSONB so
    new flags do not need migrations.
  * **No live provider calls.** Stripe-style integration is wired through a
    Protocol with a local-dev stub and an explicit alpha/staging disabled
    provider. Production deployments register a real provider behind the same
    interface; this repo never embeds keys.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api.tenant import set_tenant_context
from nextballup_core.constants import ErrorCode
from nextballup_core.enums import BillingAccountStatus, SubscriptionStatus, VideoStatus
from nextballup_core.errors import AppError, ForbiddenError
from nextballup_core.settings import Settings
from nextballup_db.models.billing import (
    AccountTeamLink,
    BillingAccount,
    Plan,
    Subscription,
    UsageEvent,
)
from nextballup_db.models.team import Team
from nextballup_db.models.video import Video

_PLAN_SNAPSHOT_FIELDS = (
    "max_videos_per_month",
    "max_storage_gb",
    "max_teams",
    "raw_video_retention_days",
)


def _plan_quotas_snapshot(plan: Plan) -> dict[str, Any]:
    return {
        **{field: getattr(plan, field) for field in _PLAN_SNAPSHOT_FIELDS},
        "features": dict(plan.features or {}),
    }


def _subscription_plan_code(subscription: Subscription | None, plan: Plan | None) -> str | None:
    if subscription is not None and subscription.plan_code_at_activation is not None:
        return subscription.plan_code_at_activation
    return plan.code if plan is not None else None


def _subscription_plan_tier(subscription: Subscription | None, plan: Plan | None) -> int:
    if subscription is not None and subscription.plan_tier_at_activation is not None:
        return subscription.plan_tier_at_activation
    return plan.tier if plan is not None else 0


# ---------- Provider abstraction ----------------------------------------------


@dataclass(frozen=True)
class CheckoutSession:
    """Result of a checkout-session creation request."""

    checkout_url: str
    external_session_id: str


class BillingProvider(Protocol):
    """Minimum surface a provider implementation must expose.

    The stub provider (below) returns deterministic, clearly-fake data so
    tests and local dev can exercise the surrounding code paths without a
    network. Production deployments register a Stripe-backed implementation.
    """

    name: str

    def create_checkout_session(
        self,
        *,
        billing_account_id: uuid.UUID,
        plan_code: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession: ...

    def cancel_subscription(self, *, external_subscription_id: str) -> None: ...


class StubBillingProvider:
    """No-op provider used for local dev, tests, and as a registration
    placeholder until a production deployment wires a real provider.

    Calls return obviously-fake URLs so a misconfigured production deploy
    cannot mistake the stub for a working integration.
    """

    name = "stub"

    def create_checkout_session(
        self,
        *,
        billing_account_id: uuid.UUID,
        plan_code: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        return CheckoutSession(
            checkout_url=f"about:blank#stub-checkout-{billing_account_id}-{plan_code}",
            external_session_id=f"stub_session_{billing_account_id}",
        )

    def cancel_subscription(self, *, external_subscription_id: str) -> None:
        return None


class BillingDisabledProvider:
    """Fail-closed provider for private alpha/staging channels.

    Alpha POC users should be able to upload and evaluate internal videos
    without any checkout surface. If a route accidentally tries to start paid
    billing while this provider is configured, the request is rejected instead
    of returning a fake URL.
    """

    name = "billing_disabled"

    def create_checkout_session(
        self,
        *,
        billing_account_id: uuid.UUID,
        plan_code: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        raise ForbiddenError("Billing is disabled for this deployment")

    def cancel_subscription(self, *, external_subscription_id: str) -> None:
        raise ForbiddenError("Billing is disabled for this deployment")


_PROVIDER_FACTORY: dict[str, Callable[[Settings], BillingProvider]] = {
    "stub": lambda _s: StubBillingProvider(),
    "billing_disabled": lambda _s: BillingDisabledProvider(),
}


def register_billing_provider(name: str, factory: Callable[[Settings], BillingProvider]) -> None:
    _PROVIDER_FACTORY[name] = factory


def get_billing_provider(settings: Settings) -> BillingProvider:
    factory = _PROVIDER_FACTORY.get(settings.billing_provider)
    if factory is None:
        raise RuntimeError(f"No billing provider registered for `{settings.billing_provider}`")
    return factory(settings)


# ---------- Tenant-context binding for RLS ------------------------------------


async def set_billing_account_context(session: AsyncSession, account_id: uuid.UUID | None) -> None:
    """Bind / clear `app.current_billing_account_id` for the session.

    `account_id=None` clears the GUC so subsequent reads on account-scoped
    tables fall through to admin-only or owner-only paths. Tenant team-scoped
    code is unaffected.
    """
    value = "" if account_id is None else str(account_id)
    await session.execute(
        text("SELECT set_config('app.current_billing_account_id', :v, true)").bindparams(v=value)
    )


# ---------- Account / subscription resolution ---------------------------------


@dataclass(frozen=True)
class AccountSubscription:
    account: BillingAccount
    subscription: Subscription | None
    plan: Plan | None


async def get_or_create_account_for_team(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
    owner_user_id: uuid.UUID | None,
    settings: Settings,
) -> BillingAccount:
    """Resolve the billing account for a team, creating one + a free
    subscription if none exists.

    A team is bound to exactly one billing account by `account_team_links`
    (UNIQUE on team_id). The link is created the first time the team needs
    billing context — typically at video-upload init.
    """
    await set_tenant_context(session, team_id)
    team_lock = await session.scalar(select(Team.id).where(Team.id == team_id).with_for_update())
    if team_lock is None:
        raise RuntimeError(f"Cannot provision billing account for unknown team `{team_id}`")

    link = await session.scalar(select(AccountTeamLink).where(AccountTeamLink.team_id == team_id))
    if link is not None:
        await set_billing_account_context(session, link.billing_account_id)
        account = await session.get(BillingAccount, link.billing_account_id)
        if account is not None:
            return account

    # No link — provision a new account and a default-plan subscription.
    account_id = uuid.uuid4()
    await set_billing_account_context(session, account_id)
    account = BillingAccount(
        id=account_id,
        name=f"team-{team_id}",
        owner_user_id=owner_user_id,
        status=BillingAccountStatus.ACTIVE,
    )
    session.add(account)
    await session.flush()
    await set_billing_account_context(session, account.id)

    link = AccountTeamLink(billing_account_id=account.id, team_id=team_id)
    session.add(link)

    plan = await session.scalar(select(Plan).where(Plan.code == settings.billing_default_plan_code))
    if plan is None:
        raise RuntimeError(
            f"Default billing plan `{settings.billing_default_plan_code}` is missing — "
            "the seed migration may not have run."
        )
    now = datetime.now(tz=UTC)
    subscription = Subscription(
        billing_account_id=account.id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=now,
        # Free/local plans still enforce monthly quotas, so the default
        # bootstrap period must be monthly rather than a year-long aggregate.
        current_period_end=now + timedelta(days=30),
        plan_code_at_activation=plan.code,
        plan_tier_at_activation=plan.tier,
        plan_quotas_at_activation=_plan_quotas_snapshot(plan),
    )
    session.add(subscription)
    await session.flush()
    return account


async def get_active_subscription(
    session: AsyncSession, *, billing_account_id: uuid.UUID
) -> AccountSubscription | None:
    account = await session.get(BillingAccount, billing_account_id)
    if (
        account is None
        or account.deleted_at is not None
        or account.status is not BillingAccountStatus.ACTIVE
    ):
        return None
    sub = await session.scalar(
        select(Subscription)
        .where(
            Subscription.billing_account_id == billing_account_id,
            Subscription.status.in_((SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE)),
        )
        .order_by(Subscription.current_period_start.desc())
    )
    plan = None
    if sub is not None:
        plan = await session.get(Plan, sub.plan_id)
    return AccountSubscription(account=account, subscription=sub, plan=plan)


async def _lock_active_billing_account(
    session: AsyncSession,
    *,
    billing_account_id: uuid.UUID,
) -> BillingAccount | None:
    account: BillingAccount | None = await session.scalar(
        select(BillingAccount)
        .where(
            BillingAccount.id == billing_account_id,
            BillingAccount.status == BillingAccountStatus.ACTIVE,
            BillingAccount.deleted_at.is_(None),
        )
        .with_for_update()
    )
    return account


# ---------- Entitlement / quota helpers ---------------------------------------


def feature_enabled(
    plan: Plan | None,
    feature_key: str,
    *,
    default: bool = False,
    subscription: Subscription | None = None,
) -> bool:
    """JSONB-flag lookup with a default for plan-less code paths."""
    if subscription is not None and subscription.plan_quotas_at_activation is not None:
        features = subscription.plan_quotas_at_activation.get("features") or {}
        if isinstance(features, dict):
            return bool(features.get(feature_key, default))
    if plan is None or not plan.features:
        return default
    value = plan.features.get(feature_key, default)
    return bool(value)


def numeric_entitlement(
    plan: Plan | None,
    attribute: str,
    *,
    subscription: Subscription | None = None,
) -> int | None:
    """Read a numeric quota off a plan, returning None for unlimited or
    when the plan is unknown."""
    if subscription is not None and subscription.plan_quotas_at_activation is not None:
        value = subscription.plan_quotas_at_activation.get(attribute)
        return None if value is None else int(value)
    if plan is None:
        return None
    return getattr(plan, attribute, None)


def raw_video_retention_days(
    plan: Plan | None,
    *,
    subscription: Subscription | None = None,
) -> int | None:
    return numeric_entitlement(plan, "raw_video_retention_days", subscription=subscription)


async def videos_used_this_period(
    session: AsyncSession, *, billing_account_id: uuid.UUID, period_start: datetime
) -> int:
    used = int(
        await session.scalar(
            select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
                UsageEvent.billing_account_id == billing_account_id,
                UsageEvent.event_key == "video.upload.initiated",
                UsageEvent.occurred_at >= period_start,
            )
        )
        or 0
    )
    return max(0, used)


async def storage_bytes_reserved(session: AsyncSession, *, billing_account_id: uuid.UUID) -> int:
    """Bytes of tenant video storage currently reserved by an account.

    Pending uploads count as reserved until they fail or are abandoned, so
    clients cannot mint presigned URLs beyond the plan's storage ceiling.
    """
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(Video.file_size_bytes), 0))
            .select_from(Video)
            .join(AccountTeamLink, AccountTeamLink.team_id == Video.team_id)
            .where(
                AccountTeamLink.billing_account_id == billing_account_id,
                Video.file_size_bytes.is_not(None),
                Video.status != VideoStatus.FAILED,
            )
        )
        or 0
    )


async def record_usage(
    session: AsyncSession,
    *,
    billing_account_id: uuid.UUID,
    event_key: str,
    quantity: int = 1,
    team_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> UsageEvent:
    event = UsageEvent(
        billing_account_id=billing_account_id,
        team_id=team_id,
        event_key=event_key,
        quantity=quantity,
        occurred_at=datetime.now(tz=UTC),
        event_metadata=metadata,
    )
    session.add(event)
    await session.flush()
    return event


async def release_video_upload_quota_reservation(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
    video_id: uuid.UUID,
    reason: str,
) -> UsageEvent | None:
    """Release the monthly upload slot reserved at presign time.

    The platform reserves one upload-count unit before issuing storage URLs so
    clients cannot mint unlimited presigned uploads. If that upload never
    finalizes and is explicitly cancelled or abandoned, we write a compensating
    usage event instead of mutating the original ledger row.
    """
    plan_ctx = await resolve_team_plan(session, team_id=team_id)
    if plan_ctx is None:
        return None
    return await record_usage(
        session,
        billing_account_id=plan_ctx.account_id,
        event_key="video.upload.initiated",
        quantity=-1,
        team_id=team_id,
        metadata={"video_id": str(video_id), "reason": reason},
    )


@dataclass(frozen=True)
class QuotaCheck:
    plan_code: str | None
    quota_key: str
    used: int
    limit: int | None  # None = unlimited
    allowed: bool


async def check_video_upload_quota(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    settings: Settings,
) -> QuotaCheck:
    """Quota check for the video-upload-initiated event.

    Returns a `QuotaCheck` with `allowed=False` when usage in the current
    subscription period would exceed the plan's `max_videos_per_month`. The
    caller decides whether to record usage / raise a ForbiddenError.
    """
    account = await get_or_create_account_for_team(
        session, team_id=team_id, owner_user_id=owner_user_id, settings=settings
    )
    locked_account = await _lock_active_billing_account(session, billing_account_id=account.id)
    if locked_account is None:
        return QuotaCheck(
            plan_code=None,
            quota_key="max_videos_per_month",
            used=0,
            limit=0,
            allowed=False,
        )
    sub = await get_active_subscription(session, billing_account_id=account.id)
    if sub is None or sub.subscription is None:
        return QuotaCheck(
            plan_code=None,
            quota_key="max_videos_per_month",
            used=0,
            limit=0,
            allowed=False,
        )
    plan = sub.plan if sub else None
    period_start = sub.subscription.current_period_start
    subscription = sub.subscription
    limit = numeric_entitlement(plan, "max_videos_per_month", subscription=subscription)
    used = await videos_used_this_period(
        session, billing_account_id=account.id, period_start=period_start
    )
    return QuotaCheck(
        plan_code=_subscription_plan_code(subscription, plan),
        quota_key="max_videos_per_month",
        used=used,
        limit=limit,
        allowed=(limit is None or used < limit),
    )


async def check_video_storage_quota(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    settings: Settings,
    additional_bytes: int,
) -> QuotaCheck:
    """Quota check for reserved video-storage bytes.

    Plans store `max_storage_gb`; the API enforces it before issuing a
    presigned upload URL so storage cost cannot be forced onto the platform by
    clients that never complete uploads.
    """
    account = await get_or_create_account_for_team(
        session, team_id=team_id, owner_user_id=owner_user_id, settings=settings
    )
    locked_account = await _lock_active_billing_account(session, billing_account_id=account.id)
    if locked_account is None:
        return QuotaCheck(
            plan_code=None,
            quota_key="max_storage_bytes",
            used=0,
            limit=0,
            allowed=False,
        )
    sub = await get_active_subscription(session, billing_account_id=account.id)
    if sub is None or sub.subscription is None:
        return QuotaCheck(
            plan_code=None,
            quota_key="max_storage_bytes",
            used=0,
            limit=0,
            allowed=False,
        )
    plan = sub.plan if sub else None
    subscription = sub.subscription
    max_storage_gb = numeric_entitlement(plan, "max_storage_gb", subscription=subscription)
    used = await storage_bytes_reserved(session, billing_account_id=account.id)
    limit = None if max_storage_gb is None else max_storage_gb * 1024 * 1024 * 1024
    return QuotaCheck(
        plan_code=_subscription_plan_code(subscription, plan),
        quota_key="max_storage_bytes",
        used=used,
        limit=limit,
        allowed=(limit is None or used + additional_bytes <= limit),
    )


def quota_exceeded_error(check: QuotaCheck) -> AppError:
    return ForbiddenError(
        f"Quota `{check.quota_key}` exceeded for plan `{check.plan_code or 'unknown'}`",
        code=ErrorCode.BILLING_QUOTA_EXCEEDED,
        details={
            "quota_key": check.quota_key,
            "limit": check.limit,
            "used": check.used,
            "plan_code": check.plan_code,
        },
    )


# ---------- Tier-aware CV artifact selection ----------------------------------
#
# Worker callers pass the registered model artifact's `min_plan_tier` (a
# small integer column added by migration 0014). Plans with a smaller tier
# than the artifact's `min_plan_tier` are rejected. The helper is a pure
# function so unit tests do not need the DB.


def artifact_meets_entitlement(*, plan: Plan | None, min_plan_tier: int) -> bool:
    if plan is None:
        return min_plan_tier <= 0
    return plan.tier >= min_plan_tier


@dataclass(frozen=True)
class TeamPlanContext:
    account_id: uuid.UUID
    plan: Plan | None
    plan_code: str | None = None
    plan_tier: int = 0
    raw_video_retention_days: int | None = None


async def resolve_team_plan(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
) -> TeamPlanContext | None:
    """Look up the active plan a team is subscribed to.

    Returns None when the team has no billing account (e.g. legacy data
    pre-migration). Workers should fail closed in that case rather than
    assume the most permissive plan.
    """
    await set_tenant_context(session, team_id)
    link = await session.scalar(select(AccountTeamLink).where(AccountTeamLink.team_id == team_id))
    if link is None:
        return None
    await set_billing_account_context(session, link.billing_account_id)
    sub = await get_active_subscription(session, billing_account_id=link.billing_account_id)
    if sub is None or sub.subscription is None:
        return None
    return TeamPlanContext(
        account_id=link.billing_account_id,
        plan=sub.plan,
        plan_code=_subscription_plan_code(sub.subscription, sub.plan),
        plan_tier=_subscription_plan_tier(sub.subscription, sub.plan),
        raw_video_retention_days=raw_video_retention_days(
            sub.plan,
            subscription=sub.subscription,
        ),
    )


def registered_provider_names() -> list[str]:
    """Sorted list of provider ids the registry knows about. Test helper."""
    return sorted(_PROVIDER_FACTORY)
