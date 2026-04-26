"""Add `min_plan_tier` to cv_model_artifacts and gate worker selection.

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-25

`min_plan_tier` is a small integer that the worker compares against the
billing-account's active plan tier. Tier 0 (free) is the implicit default
so existing rows continue to be selectable by every plan; raising the
required tier on a new artifact restricts it to higher-tier subscriptions.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cv_model_artifacts",
        sa.Column(
            "min_plan_tier",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_index(
        "ix_cv_model_artifacts_stage_tier",
        "cv_model_artifacts",
        ["stage", "min_plan_tier"],
    )
    # Drop server default after backfill so future inserts must declare the
    # tier explicitly. Existing rows keep their backfilled `0`.
    op.alter_column("cv_model_artifacts", "min_plan_tier", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_cv_model_artifacts_stage_tier", table_name="cv_model_artifacts")
    op.drop_column("cv_model_artifacts", "min_plan_tier")
