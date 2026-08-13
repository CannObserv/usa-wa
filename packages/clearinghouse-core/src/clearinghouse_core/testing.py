"""Cross-package test utilities.

Helpers tests at every layer import directly (no fixture indirection).
Currently small — grows as more sibling-reusable test infra needs a home.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


class RecordingSession:
    """AsyncSession stand-in that records the job harness's transaction decisions.

    Returned by :func:`patch_job_runtime`; only the surface the harness — or a
    ``commit=False`` handler that owns its own transaction — actually uses is
    implemented, so a test that asserts commit-vs-rollback needs no database.
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

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[RecordingSession]:
        """Record an explicit transaction block, committing on clean exit.

        The jobs that keep ``commit=False`` because their commit is not conditional on
        success — the WSL refresh, the meeting-seed harvest — do so through
        ``async with session.begin()``, and without this they failed under the helper
        with ``AttributeError`` rather than exercising the decision under test.
        """
        try:
            yield self
        except Exception:
            await self.rollback()
            raise
        await self.commit()


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
    async def _fake_session() -> AsyncIterator[RecordingSession]:
        yield session

    def _fake_factory() -> Any:
        """Stand in for ``async_sessionmaker``: calling it opens the recording session.

        Non-``None`` because the self-session jobs take their factory from
        ``ctx.require_session_factory()`` (CR #196 finding 49); handing them ``None`` here
        would fail the helper's whole purpose of letting a ``main()`` be called safely.
        """
        return _fake_session()

    @asynccontextmanager
    async def _fake_database() -> AsyncIterator[tuple[Any, RecordingSession]]:
        yield (_fake_factory, session)

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
    # Accepts the role argument the owner-role jobs pass (#179b); still callable bare.
    monkeypatch.setattr(
        job_module, "get_database_url", lambda *_a, **_k: "postgresql+asyncpg://fake/test"
    )
    return session


def parse_job_args(extra_args: Any, argv: list[str]) -> Any:
    """Parse ``argv`` exactly as ``run_job()`` would for a job declaring ``extra_args``.

    The parser moved inside the harness at #179b, so the parser tests that used to call a
    CLI's own ``_build_parser()`` need a seam. Going through the real builder is the point:
    it proves the job's flags coexist with the shared ``--dry-run`` / ``--json`` rather
    than testing a parser the CLI no longer uses.

    ``job.build_parser`` is public rather than private precisely because this helper — a
    shipped module, not a test — depends on it (CR #196 finding 51); reaching through the
    underscore would have made a rename here break a public surface with no signal.

    Local import for the same reason as :func:`patch_job_runtime` above:
    :mod:`clearinghouse_core.job` pulls in the ORM models, and this module is imported at
    conftest time before the test engine exists. The repo's "no inline imports" rule
    yields to that ordering constraint here, as it does there.
    """
    from clearinghouse_core import job as job_module

    return job_module.build_parser("test-job", None, None, extra_args).parse_args(argv)


_UNREMEMBERED = object()
"""Distinguishes "nobody has pinned a DSN" from "there is no production DSN"."""

_PRODUCTION_URL: str | None | object = _UNREMEMBERED
"""The production DSN belts 1 and 3 compare against. See :func:`remember_production_url`."""


def remember_production_url(url: str | None) -> None:
    """Pin the DSN :func:`assert_test_url_safety` treats as production.

    The root ``conftest.py`` rewrites ``DATABASE_URL`` to an unroutable sentinel for the
    whole test session (CR #191 finding 1), so that a CLI built on ``run_job()`` cannot
    commit to production when a test calls its ``main()``. But belts 1 and 3 below
    *derive* what to forbid from that same variable — read at call time, they saw
    ``blocked`` and accepted anything, including a DSN connecting as the production role
    (CR #196 finding 13). Callers that neuter the environment hand the real DSN here
    first, so the guard keeps its teeth for every later call.

    ``None`` is a **meaningful** pin, not a reset: it records "the environment named no
    production database". Falling back to ``DATABASE_URL`` in that state would read the
    sentinel the caller just installed, reintroducing the bug this exists to fix.
    """
    global _PRODUCTION_URL
    _PRODUCTION_URL = url


def production_database_url() -> str | None:
    """The DSN the guard will compare against: the pin if one was made, else the
    environment (so the helper still works outside a session that installs one)."""
    if _PRODUCTION_URL is not _UNREMEMBERED:
        return _PRODUCTION_URL  # type: ignore[return-value]
    return os.environ.get("DATABASE_URL")


def assert_test_url_safety(test_url: str) -> None:
    """Raise if ``test_url`` could reach production data.

    Defence-in-depth for destructive tests: any test that opens its own engine
    against ``TEST_DATABASE_URL`` (bypassing the savepointed ``db_session``
    fixture) must call this before issuing DDL or DML. Without it, a
    misconfigured env var can land production data under the test's cleanup
    DELETEs.

    Three independent belts:

    1. ``test_url`` must not equal the production DSN.
    2. The test database name must end in ``_test`` — catches a typo pointing
       the test DSN at the prod database even when no production DSN is known.
    3. The test DSN must not connect as the *same role* the production DSN uses.
       The forbidden role is derived from that DSN's username rather than
       hardcoded, so this stays jurisdiction-agnostic and self-maintaining for
       sibling deployments.

    Belts 1 and 3 read :func:`production_database_url`, **not** ``os.environ``
    directly — the test session deliberately neuters ``DATABASE_URL``, and comparing
    against the sentinel silently disabled both (CR #196 finding 13).

    Intentionally callable at module-import time *and* at test-body time so
    callers can re-assert immediately before any destructive operation.
    """
    prod_url = production_database_url()
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


# --- Test-database session lock (#208) ---------------------------------------

_DEFAULT_LOCK_TIMEOUT_SECONDS = 600.0


def _lock_timeout_seconds() -> float:
    """How long a queued session waits, from ``TEST_DATABASE_LOCK_TIMEOUT`` (seconds).

    The default is generous — a full suite queuing behind another (~a few minutes)
    must not spuriously fail — but finite, so a wedged holder surfaces as a clear
    error rather than a silent hang.
    """
    return float(os.environ.get("TEST_DATABASE_LOCK_TIMEOUT", _DEFAULT_LOCK_TIMEOUT_SECONDS))


def advisory_lock_key(database_url: str) -> int:
    """Stable signed-bigint advisory-lock key for ``database_url``'s database.

    Derived from the database *name* alone: every worktree pointing its
    ``TEST_DATABASE_URL`` at the same database must land on the same key,
    whatever role or host spelling its DSN uses. (Postgres advisory locks are
    already database-local, so the hash is about stability, not isolation.)
    """
    name = make_url(database_url).database or ""
    digest = hashlib.sha256(f"clearinghouse-test-db:{name}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _busy_message(timeout_seconds: float) -> str:
    return (
        "another pytest session holds the test database: gave up waiting for the "
        f"session advisory lock after {timeout_seconds:g}s. A concurrent pytest run "
        "(another worktree?) is using TEST_DATABASE_URL — wait for it to finish, or "
        "raise TEST_DATABASE_LOCK_TIMEOUT (seconds)."
    )


async def _acquire_advisory_lock(database_url: str, key: int, timeout_seconds: float) -> Any:
    """Connect raw asyncpg and block on ``pg_advisory_lock`` under ``lock_timeout``.

    ``lock_timeout`` applies to advisory-lock waits too, so the queueing is fair
    (no try/sleep polling) and the expiry is server-enforced. asyncpg autocommits,
    so the holder connection never sits idle-in-transaction for the whole session.
    """
    dsn = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    conn = await asyncpg.connect(dsn)
    try:
        millis = max(int(timeout_seconds * 1000), 1)
        await conn.execute("SELECT set_config('lock_timeout', $1, false)", str(millis))
        await conn.execute("SELECT pg_advisory_lock($1)", key)
    except asyncpg.exceptions.LockNotAvailableError as exc:
        await conn.close()
        raise RuntimeError(_busy_message(timeout_seconds)) from exc
    except BaseException:
        await conn.close()
        raise
    return conn


class DbSessionLock:
    """A Postgres session-level advisory lock held for a whole pytest session (#208).

    Serializes concurrent pytest sessions (two worktrees, say) against the shared
    test database: the session fixtures and :func:`reset_migration_schemas` DROP +
    recreate every declared schema at session boundaries, which silently corrupts a
    sibling session mid-run.

    Connection lifetime is the crux. A session-level advisory lock belongs to the
    *connection* that took it: ``async with engine.begin()`` returns the connection
    at block exit, and even ``conn.close()`` on a pooled SQLAlchemy connection only
    checks it back into the pool. So the lock lives on a dedicated raw asyncpg
    connection — owned by a private event loop on a daemon thread, because no pytest
    event loop lives long enough to own it: the integration callers reach
    :func:`reset_migration_schemas` from throwaway ``asyncio.run`` loops, and a
    connection bound to a dead loop can be neither used nor cleanly closed.

    The public API is synchronous and safe to call from inside a running event loop
    (the work happens on the holder thread); the deliberate block *is* the
    serialization. Should release never run, both backstops are structural: the
    thread is a daemon, and Postgres frees session advisory locks on disconnect.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._conn: Any = None
        self._key: int | None = None

    @property
    def held(self) -> bool:
        """Whether this process currently holds the lock through this instance."""
        return self._conn is not None

    def acquire(self, database_url: str, timeout_seconds: float | None = None) -> None:
        """Take the lock, waiting up to ``timeout_seconds`` (default: env, then 600s).

        Reentrant per *process*, by flag: session advisory locks are
        connection-scoped, so a second ``pg_advisory_lock`` on a fresh connection
        while our holder connection has the key would queue behind ourselves until
        timeout — a self-deadlock. One test database per process: a call naming a
        different database while held is a hard error, never a silent unlocked run.

        Raises :class:`RuntimeError` with an "another pytest session holds the test
        database" message when the wait expires.
        """
        if timeout_seconds is None:
            timeout_seconds = _lock_timeout_seconds()
        key = advisory_lock_key(database_url)
        if self.held:
            if key != self._key:
                raise RuntimeError(
                    "the test-database session lock is already held for a different "
                    f"database; one test database per process ({database_url!r})"
                )
            return
        assert_test_url_safety(database_url)
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, name="test-db-lock", daemon=True)
        thread.start()
        future = asyncio.run_coroutine_threadsafe(
            _acquire_advisory_lock(database_url, key, timeout_seconds), loop
        )
        try:
            # The server-side lock_timeout is the real limit; the margin only covers
            # a connect that hangs before the lock wait even starts.
            conn = future.result(timeout=timeout_seconds + 60.0)
        except TimeoutError as exc:
            future.cancel()
            self._stop_thread(loop, thread)
            raise RuntimeError(_busy_message(timeout_seconds)) from exc
        except BaseException:
            self._stop_thread(loop, thread)
            raise
        self._loop, self._thread, self._conn, self._key = loop, thread, conn, key

    def release(self) -> None:
        """Close the holder connection (disconnect frees the lock) and its thread.

        No-op when not held, so the session-end hook can call it unconditionally.
        """
        if self._conn is None or self._loop is None or self._thread is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._conn.close(), self._loop)
        future.result(timeout=30.0)
        self._stop_thread(self._loop, self._thread)
        self._loop = self._thread = self._conn = self._key = None

    @staticmethod
    def _stop_thread(loop: asyncio.AbstractEventLoop, thread: threading.Thread) -> None:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5.0)
        if not thread.is_alive():
            loop.close()


_SESSION_DB_LOCK = DbSessionLock()
"""The one holder per process. Both drop-everything routes — the ``test_engine``
fixture and :func:`reset_migration_schemas` — acquire through this, so whichever
runs first takes the lock and the other sees it held."""


def acquire_test_db_lock(database_url: str, timeout_seconds: float | None = None) -> None:
    """Acquire the process-wide test-database session lock (see :class:`DbSessionLock`)."""
    _SESSION_DB_LOCK.acquire(database_url, timeout_seconds)


def release_test_db_lock() -> None:
    """Release the process-wide lock; the root ``conftest_db`` calls this at
    ``pytest_sessionfinish`` (``trylast`` — after fixture teardown's final drops)."""
    _SESSION_DB_LOCK.release()


async def reset_migration_schemas(database_url: str) -> None:
    """Drop ``alembic_version`` + every declared schema CASCADE — no recreate.

    Pre-state reset for integration tests that clear ``alembic_version`` and
    re-run ``alembic upgrade head`` from base. Each migration recreates its own
    schema (``CREATE SCHEMA IF NOT EXISTS``), so we only drop here. Leaving any
    declared schema in place makes the from-base replay collide on its tables
    (issue #26). Reasserts the URL-safety guard before issuing DDL because this
    opens its own engine, bypassing the savepointed ``db_session`` fixture.

    Concurrency (#208): takes the process-wide session lock before dropping. Two
    deliberate cases. (a) This process already holds it — the ``test_engine``
    fixture acquired at session start — and the acquire is a flag-check no-op,
    *not* a second ``pg_advisory_lock`` on a fresh connection, which would queue
    behind our own holder until timeout. (b) An integration-only session that
    never resolved ``test_engine`` acquires here and keeps holding until
    ``pytest_sessionfinish`` — session-length on purpose, not drop-length: the
    caller's from-base migration replay right after this is exactly what a
    concurrent session's schema drop would corrupt. The acquire is a blocking
    call inside a coroutine, also on purpose — the wait is the serialization,
    and the holder does its work on its own thread.
    """
    assert_test_url_safety(database_url)
    acquire_test_db_lock(database_url)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
            for schema in declared_schemas():
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    finally:
        await engine.dispose()
