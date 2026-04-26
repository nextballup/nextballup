from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nextballup_core.enums import BillingAccountStatus, SubscriptionStatus
from nextballup_db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Catalog row for a subscription plan.

    Plans are intentionally schema-driven: numeric quotas live as columns so
    queries can enforce them in SQL, while richer feature flags live in the
    `features` JSONB so adding capabilities does not require a migration.

    `code` is stable and human-readable (e.g. `free`, `pro`); the UI surfaces
    `display_name`. The integer `tier` lets the worker pick artifacts whose
    `min_plan_tier <= subscription.plan.tier`.
    """

    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    monthly_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    annual_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Quota columns — None means "unlimited" for that quota.
    max_videos_per_month: Mapped[int | None] = mapped_column(Integer)
    max_storage_gb: Mapped[int | None] = mapped_column(Integer)
    max_teams: Mapped[int | None] = mapped_column(Integer)
    raw_video_retention_days: Mapped[int | None] = mapped_column(Integer)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (Index("ix_plans_tier", "tier"),)


class BillingAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An account that holds a subscription and pays the bill.

    A billing account aggregates one or more teams. Today the create flow
    auto-provisions a free account when a coach creates their first team;
    later, an org-purchase path will create the account up-front.

    `external_customer_id` is intentionally provider-agnostic so a Stripe-only
    rollout doesn't lock the schema.
    """

    __tablename__ = "billing_accounts"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[BillingAccountStatus] = mapped_column(
        Enum(
            BillingAccountStatus,
            name="billing_account_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=BillingAccountStatus.ACTIVE,
    )
    external_customer_id: Mapped[str | None] = mapped_column(String(255))
    billing_email: Mapped[str | None] = mapped_column(String(255))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_billing_accounts_owner", "owner_user_id"),
        Index("ix_billing_accounts_status", "status"),
    )


class AccountTeamLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Link table — a team belongs to exactly one billing account at a time.

    `team_id` is unique so a team cannot accidentally double-bill. Moves
    between accounts are explicit (delete + insert) and audit-logged.
    """

    __tablename__ = "account_team_links"

    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("team_id", name="uq_account_team_links_team"),
        Index("ix_account_team_links_account", "billing_account_id"),
    )


class Subscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The active or historical subscription a billing account holds.

    Only one row per (account, plan, current_period_start) so retroactive
    edits to a single subscription don't duplicate billing periods.
    """

    __tablename__ = "subscriptions"

    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(
            SubscriptionStatus,
            name="subscription_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=SubscriptionStatus.TRIALING,
    )
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    external_subscription_id: Mapped[str | None] = mapped_column(String(255))
    plan_code_at_activation: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_tier_at_activation: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_quotas_at_activation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "billing_account_id",
            "current_period_start",
            "plan_id",
            name="uq_subscriptions_account_period_plan",
        ),
        Index("ix_subscriptions_account_status", "billing_account_id", "status"),
    )


class UsageEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable record of a metered or quota-bearing event.

    Append-only at the database layer. The migration-owned trigger rejects
    UPDATE and DELETE so billing reconciliation cannot be rewritten by an
    application bug or an overprivileged runtime role.
    """

    __tablename__ = "usage_events"

    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
    )
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        ForeignKeyConstraint(
            ["billing_account_id"],
            ["billing_accounts.id"],
            ondelete="CASCADE",
            name="fk_usage_events_billing_account",
        ),
        Index(
            "ix_usage_events_account_key_time",
            "billing_account_id",
            "event_key",
            "occurred_at",
        ),
    )
