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
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Import Base *and* trigger model registration in every workspace package
# that defines tables. As new packages are added, list them here so their
# tables appear in Base.metadata before tests collect schemas.
import clearinghouse_sync_powermap  # noqa: F401  (registers sync-schema tables)
from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.models import Base  # noqa: F401
from clearinghouse_core.testing import assert_test_url_safety
from clearinghouse_domain_legislative import identity as _identity  # noqa: F401

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL is not set. "
        "Load env: export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)"
    )

assert_test_url_safety(TEST_DATABASE_URL)


def _declared_schemas() -> set[str]:
    """All Postgres schemas referenced by any table in Base.metadata."""
    return {t.schema for t in Base.metadata.tables.values() if t.schema}


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
async def test_engine():
    """Session-scoped engine. Creates schemas + tables once; drops on teardown.

    Explicitly DROP + recreate each declared schema at startup so the test
    session is independent of any prior state (e.g., a manual
    ``alembic upgrade head`` against ``TEST_DATABASE_URL`` outside the test
    lifecycle, which leaves seeded rows that collide with per-test fixtures).
    Also drops ``public.alembic_version`` so an alembic-managed shape doesn't
    fight with ``Base.metadata.create_all``.
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    schemas = _declared_schemas()
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
        for schema in schemas:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
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


@pytest.fixture
async def usa_wa(db_session) -> Jurisdiction:
    """Seed (or fetch) the ``usa-wa`` Jurisdiction cache row for canonical tests.

    Canonical tables FK their ``jurisdiction_id`` to
    ``clearinghouse_core.jurisdictions.id``. Tests that build canonical rows
    use ``jurisdiction_id=usa_wa.id`` instead of the prior ``"usa-wa"`` text
    literal. Per-test savepoint rollback keeps inserts isolated.

    Idempotent: looks up by slug first because the test DB may carry rows
    from a prior ``alembic upgrade head`` run outside the test_engine
    lifecycle — ``Base.metadata.create_all`` no-ops on existing tables, so
    seeded rows survive into the next test session unless the teardown
    CASCADE-drop ran.
    """
    existing = (
        await db_session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-wa"))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    state_type = (
        await db_session.execute(select(JurisdictionType).where(JurisdictionType.slug == "state"))
    ).scalar_one_or_none()
    if state_type is None:
        state_type = JurisdictionType(slug="state", display_name="State")
        db_session.add(state_type)
        await db_session.flush()
    row = Jurisdiction(
        slug="usa-wa",
        name="Washington State",
        type_id=state_type.id,
        recorded_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.flush()
    return row
