from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from nextballup_core.settings import Settings, get_settings


@asynccontextmanager
async def worker_session(settings: Settings | None = None) -> AsyncIterator[AsyncSession]:
    """Async session bound to a per-task engine with NullPool.

    Celery tasks run `asyncio.run(...)` per invocation. Reusing a module-level
    engine across loop boundaries causes SQLAlchemy to raise on connection
    teardown, so we build and dispose an engine per task. NullPool avoids
    holding idle connections between tasks.
    """
    resolved = settings or get_settings()
    engine = create_async_engine(resolved.runtime_database_url(), poolclass=NullPool)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            yield session
    finally:
        await engine.dispose()
