"""Add immutable subscription plan snapshots.

Revision ID: 0020
Revises: 0019
Create Date: 2026-04-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("plan_code_at_activation", sa.String(64)))
    op.add_column("subscriptions", sa.Column("plan_tier_at_activation", sa.Integer()))
    op.add_column("subscriptions", sa.Column("plan_quotas_at_activation", postgresql.JSONB))
    op.execute("SELECT set_config('app.current_user_role', 'admin', true)")
    op.execute(
        """
        UPDATE subscriptions AS s
        SET
            plan_code_at_activation = p.code,
            plan_tier_at_activation = p.tier,
            plan_quotas_at_activation = jsonb_build_object(
                'max_videos_per_month', p.max_videos_per_month,
                'max_storage_gb', p.max_storage_gb,
                'max_teams', p.max_teams,
                'raw_video_retention_days', p.raw_video_retention_days,
                'features', p.features
            )
        FROM plans AS p
        WHERE p.id = s.plan_id
        """
    )
    op.alter_column("subscriptions", "plan_code_at_activation", nullable=False)
    op.alter_column("subscriptions", "plan_tier_at_activation", nullable=False)
    op.alter_column("subscriptions", "plan_quotas_at_activation", nullable=False)
    op.create_check_constraint(
        "ck_subscriptions_plan_snapshot_not_null",
        "subscriptions",
        """
        plan_code_at_activation IS NOT NULL
        AND plan_tier_at_activation IS NOT NULL
        AND plan_quotas_at_activation IS NOT NULL
        """,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_subscriptions_plan_snapshot_not_null",
        "subscriptions",
        type_="check",
    )
    op.drop_column("subscriptions", "plan_quotas_at_activation")
    op.drop_column("subscriptions", "plan_tier_at_activation")
    op.drop_column("subscriptions", "plan_code_at_activation")
