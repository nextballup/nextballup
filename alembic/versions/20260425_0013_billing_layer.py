"""Add billing accounts, plans, subscriptions, account-team links, usage events.

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-25

The billing layer sits *above* teams: a billing account aggregates one or more
teams and holds the subscription. Tenant-scoped reads/writes against teams,
games, and videos remain unchanged — RLS policies on those tables are
unaffected. Account-scoped data (subscriptions, usage events) is gated by a
new `app.current_billing_account_id` GUC so a misconfigured route cannot
read another tenant's billing data even if RLS is forced on those tables in
production.

Plans are seeded inline as a small data migration so the platform always has
a default `free` plan. Adding/changing plans is a matter of writing a new
migration that INSERTs a row; updates use UPDATE so historical subscriptions
keep referencing the same plan id.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nextballup_app"
ACCOUNT_UUID = "NULLIF(current_setting('app.current_billing_account_id', true), '')::uuid"


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
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = "
        "'billing_account_status') THEN CREATE TYPE billing_account_status AS ENUM "
        "('active','suspended','closed'); END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = "
        "'subscription_status') THEN CREATE TYPE subscription_status AS ENUM "
        "('trialing','active','past_due','canceled','incomplete'); END IF; END $$"
    )

    op.create_table(
        "plans",
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
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("tier", sa.Integer, nullable=False),
        sa.Column("is_public", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("monthly_cents", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("annual_cents", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("max_videos_per_month", sa.Integer),
        sa.Column("max_storage_gb", sa.Integer),
        sa.Column("max_teams", sa.Integer),
        sa.Column("raw_video_retention_days", sa.Integer),
        sa.Column(
            "features",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("notes", sa.String(1000)),
        sa.UniqueConstraint("code", name="uq_plans_code"),
    )
    op.create_index("ix_plans_tier", "plans", ["tier"])

    op.create_table(
        "billing_accounts",
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
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="billing_account_status", create_type=False),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("external_customer_id", sa.String(255)),
        sa.Column("billing_email", sa.String(255)),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_billing_accounts_owner_user_id_users",
        ),
    )
    op.create_index("ix_billing_accounts_owner", "billing_accounts", ["owner_user_id"])
    op.create_index("ix_billing_accounts_status", "billing_accounts", ["status"])

    op.create_table(
        "account_team_links",
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
        sa.Column("billing_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["billing_account_id"],
            ["billing_accounts.id"],
            ondelete="CASCADE",
            name="fk_account_team_links_account_id_billing_accounts",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
            name="fk_account_team_links_team_id_teams",
        ),
        sa.UniqueConstraint("team_id", name="uq_account_team_links_team"),
    )
    op.create_index("ix_account_team_links_account", "account_team_links", ["billing_account_id"])

    op.create_table(
        "subscriptions",
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
        sa.Column("billing_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="subscription_status", create_type=False),
            nullable=False,
            server_default=sa.text("'trialing'"),
        ),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True)),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("external_subscription_id", sa.String(255)),
        sa.ForeignKeyConstraint(
            ["billing_account_id"],
            ["billing_accounts.id"],
            ondelete="CASCADE",
            name="fk_subscriptions_billing_account",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plans.id"],
            ondelete="RESTRICT",
            name="fk_subscriptions_plan",
        ),
        sa.UniqueConstraint(
            "billing_account_id",
            "current_period_start",
            "plan_id",
            name="uq_subscriptions_account_period_plan",
        ),
    )
    op.create_index(
        "ix_subscriptions_account_status",
        "subscriptions",
        ["billing_account_id", "status"],
    )

    op.create_table(
        "usage_events",
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
        sa.Column("billing_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("quantity", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_metadata", postgresql.JSONB),
        sa.ForeignKeyConstraint(
            ["billing_account_id"],
            ["billing_accounts.id"],
            ondelete="CASCADE",
            name="fk_usage_events_billing_account",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="SET NULL",
            name="fk_usage_events_team",
        ),
    )
    op.create_index(
        "ix_usage_events_account_key_time",
        "usage_events",
        ["billing_account_id", "event_key", "occurred_at"],
    )

    # ---- Account-scoped RLS on subscriptions / usage_events ----
    # Plans are public catalog rows so they stay un-RLS'd. Billing accounts
    # are looked up by owner / admin only and gated at app layer; we leave
    # them RLS-enabled but with a permissive admin/owner SELECT policy that
    # mirrors the app guard so a SQL injection cannot read other tenants.
    op.execute("ALTER TABLE billing_accounts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE billing_accounts FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY billing_accounts_select_access ON billing_accounts
            FOR SELECT
            USING (
                NULLIF(current_setting('app.current_user_role', true), '') = 'admin'
                OR id = {ACCOUNT_UUID}
                OR owner_user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY billing_accounts_write_context ON billing_accounts
            FOR ALL
            USING (
                NULLIF(current_setting('app.current_user_role', true), '') = 'admin'
                OR id = {ACCOUNT_UUID}
            )
            WITH CHECK (
                NULLIF(current_setting('app.current_user_role', true), '') = 'admin'
                OR id = {ACCOUNT_UUID}
            );
        """
    )

    for table in ("subscriptions", "usage_events", "account_team_links"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_select_access ON {table}
                FOR SELECT
                USING (
                    NULLIF(current_setting('app.current_user_role', true), '') = 'admin'
                    OR billing_account_id = {ACCOUNT_UUID}
                );
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_write_context ON {table}
                FOR ALL
                USING (
                    NULLIF(current_setting('app.current_user_role', true), '') = 'admin'
                    OR billing_account_id = {ACCOUNT_UUID}
                )
                WITH CHECK (
                    NULLIF(current_setting('app.current_user_role', true), '') = 'admin'
                    OR billing_account_id = {ACCOUNT_UUID}
                );
            """
        )

    for table in (
        "plans",
        "billing_accounts",
        "account_team_links",
        "subscriptions",
        "usage_events",
    ):
        _grant_runtime(table)

    # ---- Seed catalog plans ----
    # Future plan changes append a new migration that INSERTs additional rows
    # or UPDATEs existing ones by `code`. We never DELETE plan rows because
    # subscriptions reference them (RESTRICT).
    op.execute(
        """
        INSERT INTO plans (
            code, display_name, tier, monthly_cents, annual_cents,
            max_videos_per_month, max_storage_gb, max_teams,
            raw_video_retention_days, features
        ) VALUES
        ('free', 'Free', 0, 0, 0,
         5, 5, 1, 30,
         '{"cv_pipeline": false, "audit_export": false, "sso": false}'::jsonb),
        ('starter', 'Starter', 10, 4900, 49000,
         50, 100, 3, 90,
         '{"cv_pipeline": false, "audit_export": true, "sso": false}'::jsonb),
        ('pro', 'Pro', 20, 19900, 199000,
         500, 1000, 25, 365,
         '{"cv_pipeline": true, "audit_export": true, "sso": false}'::jsonb),
        ('enterprise', 'Enterprise', 30, 0, 0,
         NULL, NULL, NULL, 365,
         '{"cv_pipeline": true, "audit_export": true, "sso": true,
           "field_encryption": true}'::jsonb)
        """
    )


def downgrade() -> None:
    for table in (
        "subscriptions",
        "usage_events",
        "account_team_links",
        "billing_accounts",
    ):
        for suffix in ("select_access", "write_context"):
            op.execute(f"DROP POLICY IF EXISTS {table}_{suffix} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    op.drop_index("ix_usage_events_account_key_time", table_name="usage_events")
    op.drop_table("usage_events")

    op.drop_index("ix_subscriptions_account_status", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index("ix_account_team_links_account", table_name="account_team_links")
    op.drop_table("account_team_links")

    op.drop_index("ix_billing_accounts_status", table_name="billing_accounts")
    op.drop_index("ix_billing_accounts_owner", table_name="billing_accounts")
    op.drop_table("billing_accounts")

    op.drop_index("ix_plans_tier", table_name="plans")
    op.drop_table("plans")

    op.execute("DROP TYPE IF EXISTS subscription_status")
    op.execute("DROP TYPE IF EXISTS billing_account_status")
