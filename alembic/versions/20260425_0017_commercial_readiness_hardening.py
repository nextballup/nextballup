"""Commercial-readiness hardening for media and usage events.

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-25

Adds an integrity digest for generated playback artifacts and makes
usage_events append-only at the database layer. usage_events drives plan
quota and billing reconciliation, so it should have the same mutation
resistance as audit_logs rather than relying on application convention.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("storage_output_sha256", sa.String(64)))
    op.create_check_constraint(
        "ck_videos_storage_output_sha256_hex",
        "videos",
        "storage_output_sha256 IS NULL OR storage_output_sha256 ~ '^[0-9a-f]{64}$'",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_usage_event_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Usage events cannot be modified or deleted (billing integrity control)';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_usage_events_no_update
        BEFORE UPDATE ON usage_events
        FOR EACH ROW EXECUTE FUNCTION prevent_usage_event_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_usage_events_no_delete
        BEFORE DELETE ON usage_events
        FOR EACH ROW EXECUTE FUNCTION prevent_usage_event_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_usage_events_no_delete ON usage_events")
    op.execute("DROP TRIGGER IF EXISTS trg_usage_events_no_update ON usage_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_usage_event_mutation()")
    op.drop_constraint("ck_videos_storage_output_sha256_hex", "videos", type_="check")
    op.drop_column("videos", "storage_output_sha256")
