from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nextballup_core.settings import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _build_engine(database_url: str, **kwargs: Any) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        **kwargs,
    )


def get_engine() -> AsyncEngine:
    """Runtime engine. Uses the CRUD-only `DATABASE_URL_RUNTIME` when set so
    the app process can't accidentally (or maliciously, via SQL injection) run
    DDL against the owning user. Alembic keeps using `DATABASE_URL` directly
    for migrations."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = _build_engine(settings.runtime_database_url())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an AsyncSession with a per-request transaction."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def reset_engine_for_url(database_url: str) -> None:
    """Test helper: replace the engine with one bound to a different URL."""
    global _engine, _sessionmaker
    _engine = _build_engine(database_url)
    _sessionmaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
