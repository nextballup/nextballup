"""Commercial readiness follow-up controls.

Revision ID: 0024
Revises: 0023
Create Date: 2026-04-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nextballup_app"


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _role_exists() -> bool:
    if not _is_postgresql():
        return False
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE})
        .first()
    )


def _replace_csp_trigger_with_prune_gate() -> None:
    if not _is_postgresql():
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_csp_report_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('app.allow_csp_report_prune', true) = 'true' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'CSP reports cannot be modified or deleted (append-only control)';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION nextballup_prune_csp_reports(cutoff timestamptz)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        DECLARE
            deleted_count integer;
        BEGIN
            PERFORM set_config('app.allow_csp_report_prune', 'true', true);
            DELETE FROM csp_reports WHERE received_at < cutoff;
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            PERFORM set_config('app.allow_csp_report_prune', '', true);
            RETURN deleted_count;
        END;
        $$;
        """
    )
    if _role_exists():
        op.execute(
            f"GRANT EXECUTE ON FUNCTION nextballup_prune_csp_reports(timestamptz) TO {APP_ROLE}"
        )


def _restore_strict_csp_trigger() -> None:
    if not _is_postgresql():
        return
    op.execute("DROP FUNCTION IF EXISTS nextballup_prune_csp_reports(timestamptz)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_csp_report_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'CSP reports cannot be modified or deleted (append-only control)';
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def _tighten_cv_runtime_grants() -> None:
    if _role_exists():
        op.execute(f"REVOKE INSERT, UPDATE, DELETE ON TABLE cv_model_artifacts FROM {APP_ROLE}")


def _restore_cv_runtime_grants() -> None:
    if _role_exists():
        op.execute(f"GRANT INSERT, UPDATE, DELETE ON TABLE cv_model_artifacts TO {APP_ROLE}")


def upgrade() -> None:
    op.add_column("videos", sa.Column("raw_delete_requested_at", sa.DateTime(timezone=True)))
    op.add_column("videos", sa.Column("raw_delete_failed_at", sa.DateTime(timezone=True)))
    op.add_column("videos", sa.Column("raw_storage_deleted_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_videos_raw_delete_retry",
        "videos",
        ["raw_delete_requested_at", "raw_storage_deleted_at"],
    )
    op.execute(
        """
        UPDATE videos
        SET
            raw_delete_requested_at = raw_deleted_at,
            raw_storage_deleted_at = raw_deleted_at
        WHERE raw_deleted_at IS NOT NULL
          AND storage_key_raw IS NULL
        """
    )
    op.execute(
        """
        UPDATE videos
        SET
            raw_delete_requested_at = raw_deleted_at,
            raw_delete_failed_at = raw_deleted_at,
            raw_deleted_at = NULL
        WHERE raw_deleted_at IS NOT NULL
          AND storage_key_raw IS NOT NULL
        """
    )

    op.add_column("csp_reports", sa.Column("user_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_csp_reports_user_id_users",
        "csp_reports",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_csp_reports_user_received",
        "csp_reports",
        ["user_id", "received_at"],
    )
    _replace_csp_trigger_with_prune_gate()
    _tighten_cv_runtime_grants()


def downgrade() -> None:
    _restore_cv_runtime_grants()
    _restore_strict_csp_trigger()
    op.drop_index("ix_csp_reports_user_received", table_name="csp_reports")
    op.drop_constraint("fk_csp_reports_user_id_users", "csp_reports", type_="foreignkey")
    op.drop_column("csp_reports", "user_id")

    op.execute(
        """
        UPDATE videos
        SET raw_deleted_at = COALESCE(raw_storage_deleted_at, raw_deleted_at)
        WHERE raw_storage_deleted_at IS NOT NULL
        """
    )
    op.drop_index("ix_videos_raw_delete_retry", table_name="videos")
    op.drop_column("videos", "raw_storage_deleted_at")
    op.drop_column("videos", "raw_delete_failed_at")
    op.drop_column("videos", "raw_delete_requested_at")
