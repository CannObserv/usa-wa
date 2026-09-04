"""usa-wa-api test fixtures.

Inherits ``anyio_backend``, ``test_engine``, ``db_session`` from the workspace
root :file:`/conftest.py`. Adds the FastAPI ``client`` fixture used by API tests.
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from usa_wa_api.api.deps import get_db_session
from usa_wa_api.serving.load import create_serving_tables, ensure_serving_schema


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


@pytest.fixture
async def serving_schema(db_session) -> AsyncSession:
    """The disposable `serving` schema, stood up for one test.

    Created here rather than by a migration on purpose: this tier owns no state
    worth preserving, so `create_all` is the whole lifecycle (see
    `usa_wa_api.serving.schema`). Every `/api/v1` products test needs it, because
    since #313 that is where the product surface reads from.
    """
    await ensure_serving_schema(db_session)
    await create_serving_tables(db_session)
    return db_session
