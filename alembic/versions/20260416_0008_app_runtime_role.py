"""Create a CRUD-only `nextballup_app` runtime role for API + worker processes.

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-16

Hardening migration. Until now, every process spoke to Postgres as the
database owner, so a SQL-injection bug could in principle drop tables, flip
RLS policies, or truncate the audit log. This migration splits the roles:

- `nextballup` (owner): continues to run migrations. Owns all tables.
- `nextballup_app`: new, runtime-only. Gets SELECT/INSERT/UPDATE/DELETE on
  all existing + future tables in the `public` schema, plus USAGE on
  sequences and EXECUTE on functions, but *no* DDL and *no* superuser bit.
  Subject to RLS, just like the owner (FORCE ROW LEVEL SECURITY is on all
  tenant tables, so owner + app are both policy-gated).

The runtime connection string is wired via `DATABASE_URL_RUNTIME`; deployments
that haven't created the role yet fall back to `DATABASE_URL` and keep
working.

The role creation is wrapped in DO blocks so upgrade/downgrade are idempotent
even when the role already exists (dev DBs where someone provisioned the role
manually). The password is pulled from a per-environment env var at bootstrap
time by `infra/scripts/init-db.sql`; this migration just ensures the role
exists and has the right grants.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The role has a null password; infra is expected to ALTER ROLE … WITH PASSWORD
# out-of-band (docker-compose `infra/scripts/init-db.sql` or the production
# secret pipeline). A passwordless role can still connect locally over
# Unix sockets / trust auth, which is all the test setup needs.
APP_ROLE = "nextballup_app"


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOSUPERUSER;
            END IF;
        END
        $$;
        """
    )
    # Minimum schema access — USAGE lets the role reference objects, CREATE is
    # deliberately withheld so the app can't ADD TABLE / DROP TABLE.
    bind.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE};")
    bind.exec_driver_sql(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};"
    )
    bind.exec_driver_sql(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};")
    bind.exec_driver_sql(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {APP_ROLE};")
    # Future tables created by later migrations must inherit these grants
    # automatically — without ALTER DEFAULT PRIVILEGES, a migration that adds
    # a table would lock the app out of it until we remember to re-grant.
    bind.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE};"
    )
    bind.exec_driver_sql(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE};"
    )
    bind.exec_driver_sql(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {APP_ROLE};"
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Revoke in reverse order. DROP ROLE fails if the role still owns objects,
    # which is fine — nextballup_app only receives grants, never owns.
    bind.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE};"
    )
    bind.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE};"
    )
    bind.exec_driver_sql(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM {APP_ROLE};"
    )
    bind.exec_driver_sql(f"REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM {APP_ROLE};")
    bind.exec_driver_sql(f"REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE};")
    bind.exec_driver_sql(
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM {APP_ROLE};"
    )
    bind.exec_driver_sql(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE};")
    bind.exec_driver_sql(f"DROP OWNED BY {APP_ROLE};")
    bind.exec_driver_sql(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                DROP ROLE {APP_ROLE};
            END IF;
        EXCEPTION
            WHEN dependent_objects_still_exist THEN
                RAISE NOTICE 'Skipping DROP ROLE {APP_ROLE}; the cluster role still has dependencies outside this database.';
        END
        $$;
        """
    )
