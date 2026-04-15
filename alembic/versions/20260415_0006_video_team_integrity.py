"""Tie denormalized team_id columns back to parent rows.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-15

This migration hardens Phase 3's denormalized tenant columns so:
  * videos.team_id must match the parent game's team_id
  * processing_jobs.team_id must match the parent video's team_id
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_games_id_team_id", "games", ["id", "team_id"])
    op.create_unique_constraint("uq_videos_id_team_id", "videos", ["id", "team_id"])
    op.create_foreign_key(
        "fk_videos_game_id_team_games",
        "videos",
        "games",
        ["game_id", "team_id"],
        ["id", "team_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_processing_jobs_video_id_team_videos",
        "processing_jobs",
        "videos",
        ["video_id", "team_id"],
        ["id", "team_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_processing_jobs_video_id_team_videos", "processing_jobs", type_="foreignkey"
    )
    op.drop_constraint("fk_videos_game_id_team_games", "videos", type_="foreignkey")
    op.drop_constraint("uq_videos_id_team_id", "videos", type_="unique")
    op.drop_constraint("uq_games_id_team_id", "games", type_="unique")
