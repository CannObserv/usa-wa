"""usa-wa-api test fixtures.

Inherits ``anyio_backend``, ``test_engine``, ``db_session`` from the workspace
root :file:`/conftest.py`. Adds the FastAPI ``client`` fixture used by API tests.
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from usa_wa_api.api.deps import get_db_session


@pytest.fixture
async def client(test_engine, db_session) -> AsyncGenerator[AsyncClient]:
    """AsyncClient wired to the FastAPI app with the savepointed db_session."""
    from usa_wa_api.api.main import app

    async def override_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
