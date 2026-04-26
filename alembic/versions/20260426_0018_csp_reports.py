"""Add append-only CSP report ingestion table.

Revision ID: 0018
Revises: 0017
Create Date: 2026-04-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nextballup_app"


def _grant_runtime(table: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    role_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
        {"role": APP_ROLE},
    ).scalar()
    if role_exists:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {APP_ROLE}")


def upgrade() -> None:
    op.create_table(
        "csp_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("document_uri", sa.Text()),
        sa.Column("violated_directive", sa.String(256)),
        sa.Column("blocked_uri", sa.String(512)),
        sa.Column("source_file", sa.String(512)),
        sa.Column("line_number", sa.Integer()),
        sa.Column("column_number", sa.Integer()),
        sa.Column("user_agent", sa.String(512)),
        sa.Column("reporter_ip", sa.String(64)),
    )
    op.create_index("ix_csp_reports_received_at", "csp_reports", ["received_at"])
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
    op.execute(
        """
        CREATE TRIGGER trg_csp_reports_no_update
        BEFORE UPDATE ON csp_reports
        FOR EACH ROW EXECUTE FUNCTION prevent_csp_report_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_csp_reports_no_delete
        BEFORE DELETE ON csp_reports
        FOR EACH ROW EXECUTE FUNCTION prevent_csp_report_mutation();
        """
    )
    _grant_runtime("csp_reports")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_csp_reports_no_delete ON csp_reports")
    op.execute("DROP TRIGGER IF EXISTS trg_csp_reports_no_update ON csp_reports")
    op.execute("DROP FUNCTION IF EXISTS prevent_csp_report_mutation()")
    op.drop_index("ix_csp_reports_received_at", table_name="csp_reports")
    op.drop_table("csp_reports")
