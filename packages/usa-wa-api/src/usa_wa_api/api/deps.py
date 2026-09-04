"""FastAPI dependencies (database session).

The ``X-Operator-Token`` gate lived here until #313. It guarded exactly one
route, ``POST /sync/redrive``, and retired with it: an API with no mutating
route needs no operator secret, and a shared header token kept around for a
surface that no longer exists is a credential nobody rotates.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Yield an async DB session, closing it after the request completes."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
