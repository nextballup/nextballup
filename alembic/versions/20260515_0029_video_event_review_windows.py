"""Add editable review windows to video events.

Revision ID: 0029
Revises: 0028
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("video_events", sa.Column("clip_start_time_ms", sa.BigInteger()))
    op.add_column("video_events", sa.Column("clip_end_time_ms", sa.BigInteger()))
    op.create_check_constraint(
        "ck_video_events_clip_window_nonnegative",
        "video_events",
        """
        (clip_start_time_ms IS NULL OR clip_start_time_ms >= 0)
        AND (clip_end_time_ms IS NULL OR clip_end_time_ms >= 0)
        """,
    )
    op.create_check_constraint(
        "ck_video_events_clip_window_order",
        "video_events",
        """
        clip_start_time_ms IS NULL
        OR clip_end_time_ms IS NULL
        OR clip_start_time_ms < clip_end_time_ms
        """,
    )
    op.create_index(
        "ix_video_events_team_video_window",
        "video_events",
        ["team_id", "video_id", "clip_start_time_ms", "clip_end_time_ms"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_events_team_video_window", table_name="video_events")
    op.drop_constraint("ck_video_events_clip_window_order", "video_events", type_="check")
    op.drop_constraint("ck_video_events_clip_window_nonnegative", "video_events", type_="check")
    op.drop_column("video_events", "clip_end_time_ms")
    op.drop_column("video_events", "clip_start_time_ms")
