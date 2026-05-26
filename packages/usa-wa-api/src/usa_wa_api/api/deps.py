"""FastAPI dependencies (database session, auth, etc.)."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Yield an async DB session, closing it after the request completes."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
