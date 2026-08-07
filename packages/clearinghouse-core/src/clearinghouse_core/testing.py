"""Cross-package test utilities.

Helpers tests at every layer import directly (no fixture indirection).
Currently small — grows as more sibling-reusable test infra needs a home.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


class RecordingSession:
    """AsyncSession stand-in that records the job harness's transaction decisions.

    Returned by :func:`patch_job_runtime`; only the two methods the harness itself
    calls are implemented, so a test that asserts commit-vs-rollback needs no database.
    """

    def __init__(self) -> None:
        self.committed = 0
        self.rolled_back = 0

    async def commit(self) -> None:
        """Record a commit."""
        self.committed += 1

    async def rollback(self) -> None:
        """Record a rollback."""
        self.rolled_back += 1


def patch_job_runtime(monkeypatch: Any) -> RecordingSession:
    """Point :mod:`clearinghouse_core.job`'s database and ledger seams at fakes.

    A CLI built on ``run_job()`` resolves ``DATABASE_URL`` and opens a real session, so
    calling its ``main()`` in a test would reach **production** in any shell with the
    env loaded. Every such test must install this first. Returns the recording session
    so the test can assert the harness's commit/rollback decision; the job's own logic
    is exercised by calling its handler directly against the ``db_session`` fixture.

    Local import: :mod:`clearinghouse_core.job` pulls in the ORM models, and this
    module is imported at conftest time before the test engine exists.
    """
    from clearinghouse_core import job as job_module

    session = RecordingSession()

    @asynccontextmanager
    async def _fake_database() -> AsyncIterator[tuple[None, RecordingSession]]:
        yield (None, session)

    @asynccontextmanager
    async def _fake_ledger_session() -> AsyncIterator[RecordingSession]:
        yield RecordingSession()

    async def _noop_open(_session: Any, **_kwargs: Any) -> None:
        return None

    async def _noop_close(_session: Any, _run_id: Any, **_kwargs: Any) -> None:
        return None

    async def _noop_record(_session: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(job_module, "_database", _fake_database)
    monkeypatch.setattr(job_module, "_ledger_session", _fake_ledger_session)
    monkeypatch.setattr(job_module, "open_run", _noop_open)
    monkeypatch.setattr(job_module, "close_run", _noop_close)
    monkeypatch.setattr(job_module, "record_run", _noop_record)
    monkeypatch.setattr(job_module, "get_database_url", lambda: "postgresql+asyncpg://fake/test")
    return session


def assert_test_url_safety(test_url: str) -> None:
    """Raise if ``test_url`` could reach production data.

    Defence-in-depth for destructive tests: any test that opens its own engine
    against ``TEST_DATABASE_URL`` (bypassing the savepointed ``db_session``
    fixture) must call this before issuing DDL or DML. Without it, a
    misconfigured env var can land production data under the test's cleanup
    DELETEs.

    Three independent belts:

    1. ``test_url`` must not equal the production ``DATABASE_URL``.
    2. The test database name must end in ``_test`` — catches a typo pointing
       the test DSN at the prod database even when ``DATABASE_URL`` is unset.
    3. The test DSN must not connect as the *same role* the production
       ``DATABASE_URL`` uses. The forbidden role is derived from
       ``DATABASE_URL``'s username rather than hardcoded, so this stays
       jurisdiction-agnostic and self-maintaining for sibling deployments.

    Intentionally callable at module-import time *and* at test-body time so
    callers can re-assert immediately before any destructive operation.
    """
    prod_url = os.environ.get("DATABASE_URL")
    if prod_url and test_url == prod_url:
        raise RuntimeError(
            "TEST_DATABASE_URL must not equal DATABASE_URL. "
            "Destructive tests would otherwise drop or wipe production rows. "
            "Set TEST_DATABASE_URL to a dedicated test database "
            "(database name should include '_test')."
        )

    url = make_url(test_url)
    if not (url.database or "").endswith("_test"):
        raise RuntimeError(
            f"TEST_DATABASE_URL database name {url.database!r} must end in '_test'. "
            "A test DSN pointed at any other database can wipe non-test rows."
        )
    if prod_url:
        prod_role = make_url(prod_url).username
        if prod_role and url.username == prod_role:
            raise RuntimeError(
                f"TEST_DATABASE_URL must not connect as the same role as production "
                f"({prod_role!r}); use a dedicated test role (e.g. usa_wa_test_owner)."
            )


def declared_schemas() -> set[str]:
    """Every Postgres schema declared by any workspace table.

    Single source of truth for full-DB schema resets in integration tests that
    clear ``alembic_version`` and re-run ``alembic upgrade head`` from base. The
    set is derived from ``Base.metadata`` so it can never drift out of date as
    new schemas join the migration chain — the bug behind issue #26, where the
    ``sync`` schema (added in #22) was missing from hand-maintained wipe lists,
    so a from-base re-migration collided on ``sync.powermap_outbox``.

    The sibling-package imports below are a deliberate runtime dependency up the
    layer stack: this Layer-1 helper reaches its domain/sync siblings to force
    their table registration. They are kept *local* (not module-level) so that
    importing ``clearinghouse_core.testing`` stays safe without the siblings
    installed — only *calling* this function requires them, which never happens
    outside the co-installed workspace test venv. If clearinghouse-core is ever
    packaged standalone, this is the seam to revisit (e.g. inject the schema set
    from the caller). Run purely for side effects, they make the returned set
    complete regardless of the caller's own import context.
    """
    import clearinghouse_sync_powermap  # noqa: F401  (registers the sync schema)
    from clearinghouse_core.models import Base
    from clearinghouse_domain_legislative import identity  # noqa: F401  (canonical schema)

    return {t.schema for t in Base.metadata.tables.values() if t.schema}


async def reset_migration_schemas(database_url: str) -> None:
    """Drop ``alembic_version`` + every declared schema CASCADE — no recreate.

    Pre-state reset for integration tests that clear ``alembic_version`` and
    re-run ``alembic upgrade head`` from base. Each migration recreates its own
    schema (``CREATE SCHEMA IF NOT EXISTS``), so we only drop here. Leaving any
    declared schema in place makes the from-base replay collide on its tables
    (issue #26). Reasserts the URL-safety guard before issuing DDL because this
    opens its own engine, bypassing the savepointed ``db_session`` fixture.
    """
    assert_test_url_safety(database_url)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
            for schema in declared_schemas():
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    finally:
        await engine.dispose()
