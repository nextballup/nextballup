"""Configure the non-owner runtime database role after migrations.

Render exposes a single owner connection string for a managed Postgres
database. Migration 0008 creates the `nextballup_app` role and grants its
table permissions, but the runtime role still needs a per-environment
password before API/worker processes can use it. This script is intended for
the Render pre-deploy step immediately after `alembic upgrade head`.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from nextballup_core.settings import get_settings

_ROLE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


async def _set_runtime_role_password(connection: Any, role: str, password: str) -> None:
    """Set the runtime-role password using PostgreSQL's own SQL quoting.

    PostgreSQL utility statements such as ALTER ROLE do not accept bound
    parameters for PASSWORD, so a direct `PASSWORD :password` / `$1` bind fails
    on asyncpg. Generate the DDL with `format(%I, %L)` server-side to avoid
    client-side string interpolation of role names or secrets.
    """
    statement = await connection.scalar(
        text(
            "SELECT format("
            "'ALTER ROLE %I WITH LOGIN PASSWORD %L', "
            "CAST(:role AS text), "
            "CAST(:password AS text)"
            ")"
        ),
        {"role": role, "password": password},
    )
    if not statement:
        raise RuntimeError("Failed to build runtime-role password statement")
    await connection.exec_driver_sql(statement)


async def _main() -> None:
    settings = get_settings()
    role = settings.database_runtime_username
    password = settings.database_runtime_password
    if not _ROLE_RE.fullmatch(role):
        raise RuntimeError("DATABASE_RUNTIME_USERNAME must be a simple PostgreSQL role name")
    if not password:
        raise RuntimeError("DATABASE_RUNTIME_PASSWORD must be configured")

    owner_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with owner_engine.begin() as connection:
            await _set_runtime_role_password(connection, role, password)
    finally:
        await owner_engine.dispose()

    runtime_engine = create_async_engine(settings.runtime_database_url(), pool_pre_ping=True)
    try:
        async with runtime_engine.connect() as connection:
            current_user = await connection.scalar(text("SELECT current_user"))
            if current_user != role:
                raise RuntimeError(
                    f"Runtime database verification connected as `{current_user}`, expected `{role}`"
                )
    finally:
        await runtime_engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
