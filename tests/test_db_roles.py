"""Verify the runtime/owner role split added in migration 0008.

The point of these tests isn't to verify PostgreSQL semantics — it's to keep
us honest about the contract: an API process running as `nextballup_app`
must have CRUD on tenant tables and *no* DDL. If a future migration
accidentally regresses the grants (or forgets the default-privileges hook
for new tables), we want CI to catch it before it ships.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from nextballup_core.settings import Settings
from scripts.configure_runtime_db_role import _set_runtime_role_password


class _FakeRoleConnection:
    def __init__(self) -> None:
        self.scalar_statement: str | None = None
        self.scalar_params: dict[str, str] | None = None
        self.driver_statement: str | None = None

    async def scalar(self, statement: object, params: dict[str, str]) -> str:
        self.scalar_statement = str(statement)
        self.scalar_params = params
        return "ALTER ROLE nextballup_app WITH LOGIN PASSWORD 'p''ass:word'"

    async def exec_driver_sql(self, statement: str) -> None:
        self.driver_statement = statement


@pytest.mark.asyncio
async def test_configure_runtime_db_role_quotes_password_server_side() -> None:
    connection = _FakeRoleConnection()

    await _set_runtime_role_password(connection, "nextballup_app", "p'ass:word")

    assert connection.scalar_statement is not None
    assert "format('ALTER ROLE %I WITH LOGIN PASSWORD %L'" in connection.scalar_statement
    assert "CAST(:role AS text)" in connection.scalar_statement
    assert "CAST(:password AS text)" in connection.scalar_statement
    assert connection.scalar_params == {"role": "nextballup_app", "password": "p'ass:word"}
    assert connection.driver_statement == (
        "ALTER ROLE nextballup_app WITH LOGIN PASSWORD 'p''ass:word'"
    )
    assert "$1" not in connection.driver_statement
    assert ":password" not in connection.driver_statement


@pytest.mark.asyncio(loop_scope="session")
async def test_configure_runtime_db_role_password_statement_executes_with_asyncpg(
    engine: AsyncEngine,
) -> None:
    role = "nextballup_runtime_role_test"
    async with engine.begin() as connection:
        await connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        await connection.execute(text(f"CREATE ROLE {role} NOLOGIN"))
        try:
            await _set_runtime_role_password(connection, role, "p'ass:word+/=")
            has_login = await connection.scalar(
                text("SELECT rolcanlogin FROM pg_roles WHERE rolname = :role"),
                {"role": role},
            )
            assert has_login is True
        finally:
            await connection.execute(text(f"DROP ROLE IF EXISTS {role}"))


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
async def test_visibility_triggers_are_security_definer_without_runtime_table_grants(
    db_session: AsyncSession,
) -> None:
    rows = (
        await db_session.execute(
            text(
                """
                SELECT proname, prosecdef, COALESCE(array_to_string(proconfig, ','), '') AS config
                FROM pg_proc
                WHERE proname IN ('sync_team_visibility', 'sync_billing_account_visibility')
                """
            )
        )
    ).mappings()
    functions = {row["proname"]: row for row in rows}
    assert set(functions) == {"sync_team_visibility", "sync_billing_account_visibility"}
    for row in functions.values():
        assert row["prosecdef"] is True
        assert "search_path=public, pg_temp" in row["config"]

    for table in ("team_visibility", "billing_account_visibility"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            allowed = await db_session.scalar(
                text("SELECT has_table_privilege('nextballup_app', :table, :privilege)"),
                {"table": table, "privilege": privilege},
            )
            assert allowed is False, f"nextballup_app should not have {privilege} on {table}"


@pytest.mark.asyncio(loop_scope="session")
async def test_app_role_can_insert_team_while_visibility_table_stays_private(
    engine: AsyncEngine,
    db_session: AsyncSession,
) -> None:
    password = "RuntimeRoleVisibilityTest1"
    team_id = uuid.uuid4()
    async with engine.begin() as connection:
        await _set_runtime_role_password(connection, "nextballup_app", password)

    runtime_engine = create_async_engine(
        f"postgresql+asyncpg://nextballup_app:{password}@localhost:5432/nextballup_test",
        poolclass=NullPool,
    )
    try:
        async with runtime_engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.current_team_id', CAST(:team_id AS text), true)"),
                {"team_id": str(team_id)},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO teams (
                        id, name, sport, level, institution_type, season, invite_code
                    )
                    VALUES (
                        CAST(:team_id AS uuid),
                        :name,
                        CAST(:sport AS sport),
                        CAST(:level AS team_level),
                        CAST(:institution_type AS institution_type),
                        :season,
                        :invite_code
                    )
                    """
                ),
                {
                    "team_id": str(team_id),
                    "name": "Runtime Visibility Test",
                    "sport": "basketball",
                    "level": "aau_club",
                    "institution_type": "none",
                    "season": "2026",
                    "invite_code": "RLSVIS26",
                },
            )

        visible = await db_session.scalar(
            text(
                "SELECT deleted_at IS NULL FROM team_visibility "
                "WHERE team_id = CAST(:team_id AS uuid)"
            ),
            {"team_id": str(team_id)},
        )
        assert visible is True
    finally:
        await runtime_engine.dispose()
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.current_team_id', CAST(:team_id AS text), true)"),
                {"team_id": str(team_id)},
            )
            await connection.execute(
                text("DELETE FROM teams WHERE id = CAST(:team_id AS uuid)"),
                {"team_id": str(team_id)},
            )


@pytest.mark.asyncio(loop_scope="session")
async def test_app_role_can_insert_billing_account_while_visibility_table_stays_private(
    engine: AsyncEngine,
    db_session: AsyncSession,
) -> None:
    password = "RuntimeRoleBillingVisibilityTest1"
    account_id = uuid.uuid4()
    async with engine.begin() as connection:
        await _set_runtime_role_password(connection, "nextballup_app", password)

    runtime_engine = create_async_engine(
        f"postgresql+asyncpg://nextballup_app:{password}@localhost:5432/nextballup_test",
        poolclass=NullPool,
    )
    try:
        async with runtime_engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT set_config("
                    "'app.current_billing_account_id', CAST(:account_id AS text), true)"
                ),
                {"account_id": str(account_id)},
            )
            created_at = await connection.scalar(
                text(
                    """
                    INSERT INTO billing_accounts (id, name, status)
                    VALUES (
                        CAST(:account_id AS uuid),
                        :name,
                        CAST(:status AS billing_account_status)
                    )
                    RETURNING created_at
                    """
                ),
                {
                    "account_id": str(account_id),
                    "name": "Runtime Billing Visibility Test",
                    "status": "active",
                },
            )
            assert created_at is not None

        visible = await db_session.scalar(
            text(
                "SELECT deleted_at IS NULL FROM billing_account_visibility "
                "WHERE billing_account_id = CAST(:account_id AS uuid)"
            ),
            {"account_id": str(account_id)},
        )
        assert visible is True
    finally:
        await runtime_engine.dispose()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT set_config("
                    "'app.current_billing_account_id', CAST(:account_id AS text), true)"
                ),
                {"account_id": str(account_id)},
            )
            await connection.execute(
                text("DELETE FROM billing_accounts WHERE id = CAST(:account_id AS uuid)"),
                {"account_id": str(account_id)},
            )


@pytest.mark.asyncio(loop_scope="session")
async def test_password_reset_tokens_have_force_rls_and_expected_policies(
    db_session: AsyncSession,
) -> None:
    rel = (
        await db_session.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE oid = 'password_reset_tokens'::regclass"
            )
        )
    ).one()
    assert tuple(rel) == (True, True)

    rows = (
        await db_session.execute(
            text(
                """
                SELECT policyname, cmd, qual, with_check
                FROM pg_policies
                WHERE schemaname = 'public'
                  AND tablename = 'password_reset_tokens'
                """
            )
        )
    ).mappings()
    policies = {row["policyname"]: row for row in rows}
    assert set(policies) == {
        "password_reset_tokens_select_access",
        "password_reset_tokens_insert_owner",
        "password_reset_tokens_update_access",
        "password_reset_tokens_delete_admin",
    }
    assert policies["password_reset_tokens_select_access"]["cmd"] == "SELECT"
    assert "app.current_password_reset_token_hash" in (
        policies["password_reset_tokens_select_access"]["qual"] or ""
    )


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
    with pytest.raises(RuntimeError, match="DATABASE_URL_RUNTIME or DATABASE_RUNTIME_PASSWORD"):
        settings.runtime_database_url()
