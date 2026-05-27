"""Workspace-root pytest fixtures.

Defines the shared async engine + savepointed session that every package's
tests use. Per-package conftests extend this (e.g., usa-wa-api adds a FastAPI
``client`` fixture).

Session-scoped event loop: per-test loops strand asyncpg connections (each is
bound to the loop it was created in), forcing NullPool + per-test reconnect
overhead (~50 ms per test, ~14x baseline). Session scope reuses one loop +
pool for all tests in the run.

Schema setup: SQLAlchemy's ``Base.metadata.create_all`` creates tables inside
their declared schemas but never the schemas themselves. We inspect
``Base.metadata`` for all referenced schemas and ``CREATE SCHEMA IF NOT EXISTS``
each one before ``create_all`` runs. Teardown drops each schema CASCADE.
"""

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Import Base *and* trigger model registration in every workspace package
# that defines tables. As new packages are added, list them here so their
# tables appear in Base.metadata before tests collect schemas.
from clearinghouse_core.models import Base  # noqa: F401

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL is not set. "
        "Load env: export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)"
    )


def _check_test_url_safety(test_url: str) -> None:
    """Raise if test_url matches the production DATABASE_URL."""
    prod_url = os.environ.get("DATABASE_URL")
    if prod_url and test_url == prod_url:
        raise RuntimeError(
            "TEST_DATABASE_URL must not equal DATABASE_URL. "
            "Test teardown drops all model-mapped tables (Base.metadata.drop_all) "
            "and would destroy matching production data. "
            "Set TEST_DATABASE_URL to a dedicated test database "
            "(database name should include '_test')."
        )


_check_test_url_safety(TEST_DATABASE_URL)


def _declared_schemas() -> set[str]:
    """All Postgres schemas referenced by any table in Base.metadata."""
    return {t.schema for t in Base.metadata.tables.values() if t.schema}


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
async def test_engine():
    """Session-scoped engine. Creates schemas + tables once; drops on teardown."""
    engine = create_async_engine(TEST_DATABASE_URL)
    schemas = _declared_schemas()
    async with engine.begin() as conn:
        for schema in schemas:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    # Tear down by dropping each declared schema CASCADE. We deliberately skip
    # ``Base.metadata.drop_all`` here because it fails when there are circular
    # FKs (bills <-> bill_versions, v1.2) that need ``use_alter`` handling — the
    # CASCADE drop handles those naturally.
    async with engine.begin() as conn:
        for schema in schemas:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await engine.dispose()


@pytest.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession]:
    """Per-test session wrapped in a savepoint that rolls back on teardown."""
    async with test_engine.connect() as conn:
        txn = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        nested = await conn.begin_nested()

        @event.listens_for(session.sync_session, "after_transaction_end")
        def restart_savepoint(db_session, transaction):
            nonlocal nested
            if not nested.is_active:
                nested = conn.sync_connection.begin_nested()

        yield session

        await session.close()
        await txn.rollback()
