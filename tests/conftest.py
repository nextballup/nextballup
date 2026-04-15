"""Pytest fixtures for the NextBallUp backend.

Tests require a running local PostgreSQL with a `nextballup_test` database
(provisioned by `infra/scripts/init-db.sql` via `docker compose up -d`). Each
test runs inside an outer transaction that is rolled back on teardown, so no
test commits leak into shared state.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parent.parent

# Ensure async URL points at the test DB even if .env points at the dev DB.
TEST_DATABASE_URL = "postgresql+asyncpg://nextballup:nextballup_dev@localhost:5432/nextballup_test"


def _generate_rsa_pair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


async def _reset_test_schema(database_url: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.execute(text("GRANT ALL ON SCHEMA public TO PUBLIC"))
    finally:
        await engine.dispose()


def _apply_migrations(database_url: str) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


def pytest_configure(config: pytest.Config) -> None:
    """Set test environment and prepare the DB schema before the suite runs."""
    os.environ["APP_ENV"] = "test"
    os.environ["APP_DEBUG"] = "false"
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["DATABASE_URL_SYNC"] = TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql")
    os.environ["COOKIE_SECURE"] = "false"
    os.environ["REDIS_URL"] = ""
    os.environ["S3_ENDPOINT_URL"] = ""
    os.environ["S3_ACCESS_KEY"] = ""
    os.environ["S3_SECRET_KEY"] = ""
    os.environ["S3_BUCKET_RAW"] = ""
    os.environ["TRUSTED_PROXY_IPS"] = "[]"

    private_pem, public_pem = _generate_rsa_pair()
    os.environ["JWT_PRIVATE_KEY"] = private_pem
    os.environ["JWT_PUBLIC_KEY"] = public_pem
    # Path placeholders that will never be read because the in-memory keys win.
    os.environ["JWT_PRIVATE_KEY_PATH"] = "/dev/null"
    os.environ["JWT_PUBLIC_KEY_PATH"] = "/dev/null"

    # Drop the cached settings instance so the new env wins.
    from nextballup_core.settings import reload_settings

    reload_settings()

    asyncio.run(_reset_test_schema(TEST_DATABASE_URL))
    _apply_migrations(TEST_DATABASE_URL)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():  # type: ignore[no-untyped-def]
    eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(engine) -> AsyncIterator[AsyncSession]:  # type: ignore[no-untyped-def]
    """Session bound to an outer transaction; commits become SAVEPOINTs that
    are discarded when the outer transaction rolls back on teardown."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    from httpx import ASGITransport, AsyncClient
    from nextballup_api.deps import get_db
    from nextballup_api.main import app

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


# Ensure the project root is on sys.path so test modules can import unprefixed.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
