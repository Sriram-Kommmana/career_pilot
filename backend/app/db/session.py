"""Engine and session management.

One async engine per process, created lazily so importing this module does not open a
connection — which matters for unit tests and for Alembic, both of which import
application code without wanting a live database.

Transaction policy: a request gets one session and one transaction. The dependency
commits on success and rolls back on any exception, so a handler that raises halfway
through cannot leave a partial write behind. Handlers do not call ``commit()``
themselves.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings, resolve_database_url
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine_from_settings(settings: Settings, *, url: str | None = None) -> AsyncEngine:
    """Build an engine. Exposed so tests can point at the test database.

    The URL goes through :func:`resolve_database_url`, which starts the embedded local
    PostgreSQL server when ``CAREERPILOT_DATABASE_URL`` is ``embedded``.
    """
    return create_async_engine(
        url or resolve_database_url(settings),
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Recycle below the typical 10-minute idle cut-off of managed Postgres, and
        # pre-ping so a connection dropped by the server surfaces as a retry rather
        # than as a failed request.
        pool_recycle=300,
        pool_pre_ping=True,
        future=True,
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine_from_settings(get_settings())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


def configure(engine: AsyncEngine) -> None:
    """Replace the process engine. Used by the integration test fixtures."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def dispose_engine() -> None:
    """Close the pool. Called on application shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one session per request.

    Commits when the handler returns normally; rolls back if it raises. Exceptions
    propagate untouched — a failed write must surface, never be swallowed.
    """
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """The same contract as :func:`get_db_session`, for workers and scripts."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database_connection() -> bool:
    """Whether the database answers a trivial query. Used by the health endpoint."""
    from sqlalchemy import text

    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("database_unreachable", error=str(exc))
        return False
