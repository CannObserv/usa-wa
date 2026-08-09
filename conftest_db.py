"""Database fixtures for the workspace — the ``db`` tier (#185).

Registered as a plugin by the root :file:`conftest.py`, which stays database-free.
Split out so the tier boundary is a file boundary: nothing here is reachable from a
``pytest -m 'not db'`` run, and every fixture below fails at *resolution* rather than
at import, so a workspace with no ``TEST_DATABASE_URL`` still collects and runs its
pure tests.

Session-scoped event loop: per-test loops strand asyncpg connections (each is bound to
the loop it was created in), forcing NullPool + per-test reconnect overhead (~50 ms per
test, ~14x baseline). Session scope reuses one loop + pool for all tests in the run.

Schema setup: SQLAlchemy's ``Base.metadata.create_all`` creates tables inside their
declared schemas but never the schemas themselves. We inspect ``Base.metadata`` for all
referenced schemas and ``CREATE SCHEMA IF NOT EXISTS`` each one before ``create_all``
runs. Teardown drops each schema CASCADE.

Table registration: ``declared_schemas()`` imports every workspace package that
declares tables (for its own schema discovery) and is called before ``create_all``, so
``Base.metadata`` is complete by the time the DDL is emitted. That indirection is what
keeps this module's own imports to Layer 1 — the harness never names an adapter.
"""

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.models import Base
from clearinghouse_core.testing import declared_schemas


@pytest.fixture(scope="session")
async def test_engine():
    """Session-scoped engine. Creates schemas + tables once; drops on teardown.

    Explicitly DROP + recreate each declared schema at startup so the test
    session is independent of any prior state (e.g., a manual
    ``alembic upgrade head`` against ``TEST_DATABASE_URL`` outside the test
    lifecycle, which leaves seeded rows that collide with per-test fixtures).
    Also drops ``public.alembic_version`` so an alembic-managed shape doesn't
    fight with ``Base.metadata.create_all``.

    The ``TEST_DATABASE_URL`` check lives here, not at import: this is the first
    moment a database is actually required. Its *safety* is asserted at conftest
    import instead, while the real ``DATABASE_URL`` is still visible to compare
    against — see the root :file:`conftest.py`.
    """
    test_database_url = os.environ.get("TEST_DATABASE_URL")
    if not test_database_url:
        raise RuntimeError(
            "TEST_DATABASE_URL is not set, and this test needs a database. "
            "Load env: export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs) — "
            "or run the unit tier, which needs none: uv run pytest -m 'not db'"
        )
    engine = create_async_engine(test_database_url)
    schemas = declared_schemas()
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
async def drop_anchor_unique_indexes(db_session) -> None:
    """Drop the #86 one-row-per-PM-anchor partial unique indexes for one test.

    The one-shot span-collapse migrations (``migrate_sponsor_spans`` /
    ``migrate_pdc_spans`` / ``migrate_committee_spans``) exist to retire the
    *pre-#86* duplicate-anchor rows the #84 crash loop was armed by — a state the
    partial unique indexes now forbid, so those tests cannot even build the fixture
    under them. Reproduce the pre-index world by dropping the indexes; the per-test
    transaction rolls the drops back on teardown, so other tests keep the constraint.
    """
    for index in (
        "uq_persons_pm_person_id",
        "uq_organizations_pm_organization_id",
        "uq_roles_pm_role_id",
        "uq_assignments_pm_assignment_id",
    ):
        await db_session.execute(text(f"DROP INDEX IF EXISTS canonical.{index}"))


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
