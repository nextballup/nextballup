"""Add team_invites table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-15

Phase 2 migration. Adds:
  * team_invites table for coach-issued, role-/expiry-/usage-bound invite codes
  * Row-Level Security policy that ties invites to the active team context
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "team_invites",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invite_code", sa.String(20), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(name="team_role", create_type=False),
            nullable=False,
            server_default=sa.text("'player'"),
        ),
        sa.Column("max_uses", sa.Integer, nullable=False, server_default=sa.text("20")),
        sa.Column("uses", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
            name="fk_team_invites_team_id_teams",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_team_invites_created_by_users",
        ),
        sa.UniqueConstraint("invite_code", name="uq_team_invites_invite_code"),
    )
    op.create_index("ix_team_invites_invite_code", "team_invites", ["invite_code"])
    op.create_index("ix_team_invites_team_active", "team_invites", ["team_id", "is_active"])
    op.create_index("ix_team_invites_expires_at", "team_invites", ["expires_at"])

    # team_invites is tenant-scoped: the active team context decides which
    # invites a request can read/modify. Lookups by code on /teams/join must
    # bypass this filter — they happen pre-membership, so the lookup query
    # opens its own short context (see routers/teams.py).
    op.execute("ALTER TABLE team_invites ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY team_invites_tenant_isolation ON team_invites
            USING (team_id = current_setting('app.current_team_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS team_invites_tenant_isolation ON team_invites")
    op.execute("ALTER TABLE team_invites DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_team_invites_expires_at", table_name="team_invites")
    op.drop_index("ix_team_invites_team_active", table_name="team_invites")
    op.drop_index("ix_team_invites_invite_code", table_name="team_invites")
    op.drop_table("team_invites")
