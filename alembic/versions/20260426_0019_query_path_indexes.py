"""Add export and worker hot-path indexes.

Revision ID: 0019
Revises: 0018
Create Date: 2026-04-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_logs_actor_email_created_at",
        "audit_logs",
        ["actor_email", "created_at"],
    )
    op.create_index(
        "ix_cv_model_artifacts_stage_status_created",
        "cv_model_artifacts",
        ["stage", "status", sa.text("created_at DESC")],
    )
    op.drop_index("ix_processing_jobs_status", table_name="processing_jobs")
    op.create_index(
        "ix_processing_jobs_status_created",
        "processing_jobs",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_status_created", table_name="processing_jobs")
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"])
    op.drop_index(
        "ix_cv_model_artifacts_stage_status_created",
        table_name="cv_model_artifacts",
    )
    op.drop_index("ix_audit_logs_actor_email_created_at", table_name="audit_logs")
