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
import subprocess
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.models import Base
from clearinghouse_core.testing import acquire_test_db_lock, declared_schemas, release_test_db_lock

#: Production secrets (AGENTS.md § Environment Variables). Deliberately *not* where
#: ``TEST_DATABASE_URL`` lives — naming it alone is what made the old remediation
#: unfollowable (#296).
SYSTEM_ENV = "/etc/usa-wa/.env"


def _repo_env_path(project_root: Path) -> Path | None:
    """The ``.env`` that actually exists for ``project_root``, or ``None``.

    ``.env`` is git-ignored, so ``git worktree add`` never produces one and
    nothing seeds it — while AGENTS.md § Server Lifecycle mandates worktree-based
    feature work (#87). So the primary checkout's copy is the fallback, resolved
    the way ``scripts/pre-ship.sh`` and ``socraticode-health.sh`` resolve it:
    ``--git-common-dir`` is the shared ``.git`` for a worktree and for the primary
    checkout alike, and its parent is the primary checkout.
    """
    here = project_root / ".env"
    if here.is_file():
        return here
    try:
        common = subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not common:
        return None
    fallback = Path(common).parent / ".env"
    return fallback if fallback.is_file() else None


def missing_test_database_url_message(project_root: Path | None = None) -> str:
    """The error a db-marked test gets with no DSN — naming a path that exists.

    Built from the checkout it is raised in rather than from a fixed recipe. The
    old message printed ``cat /etc/usa-wa/.env .env``, which in a worktree
    expands to the one file that by design does not carry ``TEST_DATABASE_URL``:
    the reader followed it and got the identical error back.
    """
    root = Path(project_root) if project_root is not None else Path(__file__).parent
    repo_env = _repo_env_path(root)
    files = SYSTEM_ENV if repo_env is None else f"{SYSTEM_ENV} {repo_env}"
    return (
        "TEST_DATABASE_URL is not set, and this test needs a database. "
        f"Load env: export $(cat {files} 2>/dev/null | xargs) — "
        "or run the unit tier, which needs none: "
        "uv run pytest -m 'not db and not integration'"
    )


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

    Concurrency (#208): before the first DROP, take the process-wide session
    advisory lock (``clearinghouse_core.testing.DbSessionLock``). Concurrent
    pytest sessions against the shared test DB — two worktrees, say — serialize
    on it; a queued session waits up to ``TEST_DATABASE_LOCK_TIMEOUT`` seconds
    (default 600), then fails here with a clear "another pytest session holds the
    test database" error instead of silently corrupting the holder. Released by
    ``pytest_sessionfinish`` below, *after* this fixture's teardown drops.
    """
    test_database_url = os.environ.get("TEST_DATABASE_URL")
    if not test_database_url:
        raise RuntimeError(missing_test_database_url_message())
    acquire_test_db_lock(test_database_url)
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


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Release the #208 test-DB session lock once everything DB-touching is done.

    ``trylast`` is load-bearing: session-fixture teardown happens inside
    ``_pytest.runner``'s *own* ``pytest_sessionfinish``, and conftest hooks run
    before builtins — without it this would release mid-teardown, un-guarding
    ``test_engine``'s final CASCADE drops. No-op in the unit tier (never acquired).
    The lock is acquired by ``test_engine`` above or by
    ``reset_migration_schemas`` (whichever a session hits first), so the release
    lives here, at the only point common to both.
    """
    release_test_db_lock()


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
