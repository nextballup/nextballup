"""Widen video multipart upload ids.

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-06

R2 multipart UploadId values can exceed 255 characters. The value is provider
opaque and must be stored exactly so /complete can finish the same multipart
session that /upload initiated.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "videos",
        "upload_id",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Downgrades are operational rollback tooling, not a supported way to keep
    # in-flight R2 multipart sessions intact. Truncate only on downgrade so the
    # type change can be reversed without failing on provider-issued ids.
    op.execute("UPDATE videos SET upload_id = left(upload_id, 255) WHERE length(upload_id) > 255")
    op.alter_column(
        "videos",
        "upload_id",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
