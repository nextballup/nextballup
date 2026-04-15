"""Initial schema: extensions, users, teams, team_memberships, audit_logs.

Revision ID: 0001
Revises:
Create Date: 2026-04-14

Phase 1 migration. Establishes:
  * Required PostgreSQL extensions
  * Core auth/team tables (users, teams, team_memberships)
  * Append-only audit_logs (DB-level immutability trigger)
  * Row-Level Security policies on tenant-scoped tables

Production note on RLS: `ENABLE ROW LEVEL SECURITY` (without FORCE) is bypassed
by the table owner. The application must connect as a non-owner role for the
policies to actually filter rows. In Phase 1 dev — where the docker-compose
`nextballup` user is the owner — RLS is enabled and policies are defined but
the owner bypass means filtering is not enforced. Production hardening:
provision a `nextballup_app` role with CRUD-only grants and connect from there.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Enum type definitions -- created once, then reused by columns below.
USER_ROLE = postgresql.ENUM("coach", "player", "admin", name="user_role")
SPORT = postgresql.ENUM("basketball", "volleyball", name="sport")
TEAM_LEVEL = postgresql.ENUM(
    "youth",
    "aau_club",
    "middle_school",
    "high_school",
    "juco",
    "college_d3",
    "college_d2",
    "college_d1",
    "professional",
    "international",
    name="team_level",
)
INSTITUTION_TYPE = postgresql.ENUM(
    "none",
    "k12_school",
    "college",
    "club",
    "academy",
    "professional",
    name="institution_type",
)
TEAM_ROLE = postgresql.ENUM(
    "head_coach",
    "assistant_coach",
    "manager",
    "player",
    "captain",
    name="team_role",
)

TENANT_SCOPED_TABLES = ("teams", "team_memberships", "audit_logs")


def upgrade() -> None:
    bind = op.get_bind()

    # ---- Extensions ----
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "btree_gist"')

    # ---- Enum types ----
    for enum_type in (USER_ROLE, SPORT, TEAM_LEVEL, INSTITUTION_TYPE, TEAM_ROLE):
        enum_type.create(bind, checkfirst=True)

    # ---- users ----
    op.create_table(
        "users",
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
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(name="user_role", create_type=False),
            nullable=False,
        ),
        sa.Column("phone", sa.String(20)),
        sa.Column("institution", sa.String(255)),
        sa.Column("avatar_url", sa.String(1024)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("session_version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("height_inches", sa.Integer),
        sa.Column("weight_lbs", sa.Integer),
        sa.Column("position", sa.String(10)),
        sa.Column("graduation_year", sa.Integer),
        sa.Column("handedness", sa.String(10)),
        sa.Column(
            "biometric_consent",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "parental_consent_on_file",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "date_of_birth_verified",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.execute("CREATE UNIQUE INDEX ix_users_email_lower ON users (lower(email))")
    op.create_index("ix_users_role", "users", ["role"])

    # ---- teams ----
    op.create_table(
        "teams",
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
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "sport",
            postgresql.ENUM(name="sport", create_type=False),
            nullable=False,
            server_default=sa.text("'basketball'"),
        ),
        sa.Column(
            "level",
            postgresql.ENUM(name="team_level", create_type=False),
            nullable=False,
        ),
        sa.Column("institution", sa.String(255)),
        sa.Column(
            "institution_type",
            postgresql.ENUM(name="institution_type", create_type=False),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column("season", sa.String(20), nullable=False),
        sa.Column("city", sa.String(100)),
        sa.Column("state", sa.String(10)),
        sa.Column("conference", sa.String(255)),
        sa.Column("invite_code", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("invite_code", name="uq_teams_invite_code"),
    )
    op.create_index("ix_teams_invite_code", "teams", ["invite_code"])
    op.create_index("ix_teams_sport_level", "teams", ["sport", "level"])
    op.create_index("ix_teams_season", "teams", ["season"])

    # ---- team_memberships ----
    op.create_table(
        "team_memberships",
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
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "team_role",
            postgresql.ENUM(name="team_role", create_type=False),
            nullable=False,
        ),
        sa.Column("jersey_number", sa.Integer),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
            name="fk_team_memberships_team_id_teams",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_team_memberships_user_id_users",
        ),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),
        sa.UniqueConstraint("team_id", "jersey_number", name="uq_team_memberships_team_jersey"),
    )
    op.create_index("ix_team_memberships_team", "team_memberships", ["team_id"])
    op.create_index("ix_team_memberships_user", "team_memberships", ["user_id"])

    # ---- audit_logs ----
    op.create_table(
        "audit_logs",
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
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(255)),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(40)),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True)),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ip_address", postgresql.INET),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("request_id", sa.String(64)),
        sa.Column("extra", postgresql.JSONB),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_audit_logs_actor_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="SET NULL",
            name="fk_audit_logs_team_id_teams",
        ),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_action_created_at", "audit_logs", ["action", "created_at"])
    op.create_index(
        "ix_audit_logs_actor_created_at",
        "audit_logs",
        ["actor_user_id", "created_at"],
    )
    op.create_index("ix_audit_logs_team_created_at", "audit_logs", ["team_id", "created_at"])
    op.create_index("ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"])

    # ---- audit_logs immutability trigger ----
    # Function may already exist if init-db.sql ran; CREATE OR REPLACE is idempotent.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit log entries cannot be modified or deleted (SOC 2 control)';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_no_update
        BEFORE UPDATE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_no_delete
        BEFORE DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
        """
    )

    # ---- Row-Level Security on tenant-scoped tables ----
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

    # Tenant context comes from the GUC `app.current_team_id`. The `, true` form
    # of current_setting() returns NULL when the GUC is unset (rather than
    # raising), which makes default SELECTs return zero rows — fail-closed.
    op.execute(
        """
        CREATE POLICY teams_tenant_isolation ON teams
            USING (id = current_setting('app.current_team_id', true)::uuid);
        """
    )
    op.execute(
        """
        CREATE POLICY team_memberships_tenant_isolation ON team_memberships
            USING (team_id = current_setting('app.current_team_id', true)::uuid);
        """
    )
    # audit_logs: cross-tenant entries (e.g. failed login) carry NULL team_id and
    # remain visible across contexts. Tenant-bound entries are gated by the GUC.
    op.execute(
        """
        CREATE POLICY audit_logs_tenant_isolation ON audit_logs
            USING (
                team_id IS NULL
                OR team_id = current_setting('app.current_team_id', true)::uuid
            );
        """
    )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_delete ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_update ON audit_logs")
    # Leave prevent_audit_mutation() in place — it's owned by init-db.sql too.

    op.drop_index("ix_audit_logs_actor_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index("ix_audit_logs_team_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_team_memberships_user", table_name="team_memberships")
    op.drop_index("ix_team_memberships_team", table_name="team_memberships")
    op.drop_table("team_memberships")

    op.drop_index("ix_teams_season", table_name="teams")
    op.drop_index("ix_teams_sport_level", table_name="teams")
    op.drop_index("ix_teams_invite_code", table_name="teams")
    op.drop_table("teams")

    op.drop_index("ix_users_role", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_email_lower", table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    for enum_type in (TEAM_ROLE, INSTITUTION_TYPE, TEAM_LEVEL, SPORT, USER_ROLE):
        enum_type.drop(bind, checkfirst=True)
