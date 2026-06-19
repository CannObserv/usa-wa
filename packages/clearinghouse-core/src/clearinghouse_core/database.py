"""Async database engine and session factory."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from clearinghouse_core.config import get_database_url
from clearinghouse_core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the shared async engine, creating it on first call."""
    global _engine
    if _engine is None:
        url = get_database_url()
        _engine = create_async_engine(url, echo=False)
        logger.info("database engine created", extra={"host": url.split("@")[-1]})
    return _engine


def reset_engine() -> None:
    """Reset the shared engine and session factory. For testing only."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the shared session factory, creating it on first call."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def fetch_connection_fingerprint(session: AsyncSession) -> tuple[str, str]:
    """Return ``(current_user, current_database)`` for the session's connection.

    Used at startup to make role/DB confusion immediately visible — a sidecar
    or API booted against the wrong DSN announces it in its first log line.
    """
    row = (await session.execute(text("SELECT current_user, current_database()"))).one()
    return str(row[0]), str(row[1])


async def log_connection_fingerprint(session: AsyncSession, *, context: str) -> None:
    """Log the connected role + database. Best-effort: never raises on failure."""
    try:
        db_user, db_name = await fetch_connection_fingerprint(session)
    except Exception:  # noqa: BLE001 — fingerprint is diagnostic, must not block boot
        logger.warning("database fingerprint unavailable", extra={"context": context})
        return
    logger.info(
        "database connection fingerprint",
        extra={"db_user": db_user, "db_name": db_name, "context": context},
    )
