"""Capture object storage ETag and tighten output-key column widths.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-15

Phase 5 migration. The transcode worker now records the storage ETag returned
by the head_object check (when available) so a future cryptographic
verification phase has a stable reference. Output keys (mezzanine/HLS/
thumbnail) are already on the videos table from Phase 3 — this migration only
adds `storage_etag`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "videos",
        sa.Column("storage_etag", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("videos", "storage_etag")
