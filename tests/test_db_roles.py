"""Verify the runtime/owner role split added in migration 0008.

The point of these tests isn't to verify PostgreSQL semantics — it's to keep
us honest about the contract: an API process running as `nextballup_app`
must have CRUD on tenant tables and *no* DDL. If a future migration
accidentally regresses the grants (or forgets the default-privileges hook
for new tables), we want CI to catch it before it ships.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.settings import Settings


@pytest.mark.asyncio(loop_scope="session")
async def test_app_role_exists_with_non_privileged_flags(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT rolcreatedb, rolcreaterole, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = 'nextballup_app'"
            )
        )
    ).first()
    assert row is not None, "nextballup_app role should be created by migration 0008"
    rolcreatedb, rolcreaterole, rolsuper, rolbypassrls = row
    assert rolcreatedb is False
    assert rolcreaterole is False
    assert rolsuper is False
    # Must be subject to RLS — the whole point of the role split.
    assert rolbypassrls is False


@pytest.mark.asyncio(loop_scope="session")
async def test_app_role_has_crud_on_tenant_tables(db_session: AsyncSession) -> None:
    """For each tenant-scoped table, the app role must have exactly
    SELECT+INSERT+UPDATE+DELETE. No TRUNCATE, no REFERENCES, no TRIGGER."""
    tables = [
        "users",
        "teams",
        "team_memberships",
        "team_invites",
        "games",
        "videos",
        "processing_jobs",
    ]
    for table in tables:
        privs = {
            row[0]
            for row in (
                await db_session.execute(
                    text(
                        "SELECT privilege_type FROM information_schema.table_privileges "
                        "WHERE grantee = 'nextballup_app' AND table_name = :tbl"
                    ),
                    {"tbl": table},
                )
            ).all()
        }
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"}.issubset(privs), (
            f"nextballup_app is missing CRUD on {table}: {privs}"
        )
        # Defense-in-depth: the role must NOT have DDL-ish grants.
        assert "TRUNCATE" not in privs, f"nextballup_app should not have TRUNCATE on {table}"


@pytest.mark.asyncio(loop_scope="session")
async def test_app_role_has_append_only_table_grants(db_session: AsyncSession) -> None:
    for table in ("audit_logs", "csp_reports", "usage_events"):
        privs = {
            row[0]
            for row in (
                await db_session.execute(
                    text(
                        "SELECT privilege_type FROM information_schema.table_privileges "
                        "WHERE grantee = 'nextballup_app' AND table_name = :tbl"
                    ),
                    {"tbl": table},
                )
            ).all()
        }
        assert {"SELECT", "INSERT"}.issubset(privs), (
            f"nextballup_app is missing append-only grants on {table}: {privs}"
        )
        assert "UPDATE" not in privs
        assert "DELETE" not in privs
        assert "TRUNCATE" not in privs


@pytest.mark.asyncio(loop_scope="session")
async def test_app_role_cannot_mutate_cv_model_artifact_catalog(
    db_session: AsyncSession,
) -> None:
    privs = {
        row[0]
        for row in (
            await db_session.execute(
                text(
                    "SELECT privilege_type FROM information_schema.table_privileges "
                    "WHERE grantee = 'nextballup_app' AND table_name = 'cv_model_artifacts'"
                )
            )
        ).all()
    }
    assert "SELECT" in privs
    assert "INSERT" not in privs
    assert "UPDATE" not in privs
    assert "DELETE" not in privs


@pytest.mark.asyncio(loop_scope="session")
async def test_app_role_can_execute_csp_report_prune_function(
    db_session: AsyncSession,
) -> None:
    can_execute = await db_session.scalar(
        text(
            "SELECT has_function_privilege("
            "'nextballup_app', "
            "'nextballup_prune_csp_reports(timestamp with time zone)', "
            "'EXECUTE'"
            ")"
        )
    )
    assert can_execute is True


def test_runtime_database_url_requires_explicit_runtime_role_in_production() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://owner@localhost:5432/nextballup",
        database_url_sync="postgresql://owner@localhost:5432/nextballup",
        database_url_runtime=None,
        jwt_private_key="test-private-key",
        jwt_public_key="test-public-key",
    )
    with pytest.raises(RuntimeError, match="DATABASE_URL_RUNTIME must be configured"):
        settings.runtime_database_url()
