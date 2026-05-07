"""Store alpha detector preview state on videos.

Revision ID: 0028
Revises: 0027
Create Date: 2026-05-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "videos",
        sa.Column(
            "demo_preview_status",
            sa.String(length=32),
            nullable=False,
            server_default="idle",
        ),
    )
    op.add_column("videos", sa.Column("demo_preview_storage_key", sa.String(length=1024)))
    op.add_column("videos", sa.Column("demo_preview_requested_at", sa.DateTime(timezone=True)))
    op.add_column("videos", sa.Column("demo_preview_started_at", sa.DateTime(timezone=True)))
    op.add_column("videos", sa.Column("demo_preview_generated_at", sa.DateTime(timezone=True)))
    op.add_column("videos", sa.Column("demo_preview_task_id", sa.String(length=128)))
    op.add_column("videos", sa.Column("demo_preview_error_message", sa.String(length=1000)))
    op.create_check_constraint(
        "ck_videos_demo_preview_status",
        "videos",
        "demo_preview_status IN ('idle', 'queued', 'running', 'completed', 'failed')",
    )
    op.create_index(
        "ix_videos_demo_preview_status",
        "videos",
        ["demo_preview_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_videos_demo_preview_status", table_name="videos")
    op.drop_constraint("ck_videos_demo_preview_status", "videos", type_="check")
    op.drop_column("videos", "demo_preview_error_message")
    op.drop_column("videos", "demo_preview_task_id")
    op.drop_column("videos", "demo_preview_generated_at")
    op.drop_column("videos", "demo_preview_started_at")
    op.drop_column("videos", "demo_preview_requested_at")
    op.drop_column("videos", "demo_preview_storage_key")
    op.drop_column("videos", "demo_preview_status")
