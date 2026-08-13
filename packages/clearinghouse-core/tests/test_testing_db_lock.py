"""Concurrent pytest sessions must serialize on the test database (#208).

The session fixtures (and ``reset_migration_schemas``) DROP + recreate every declared
schema at session boundaries. Two sessions pointed at the same ``TEST_DATABASE_URL`` —
two worktrees, say — silently corrupt each other. The fix is a Postgres session-level
advisory lock held by a dedicated connection for the whole pytest session, with a
server-side ``lock_timeout`` that turns "waited too long" into a clear error naming
the situation.

Probe keys: the pytest session running *these* tests already holds the real advisory
key (the ``test_engine`` fixture acquires it), and session advisory locks are
connection-scoped — a fresh holder asked for the *real* key would queue behind our own
session until timeout. Every test that builds its own holder therefore monkeypatches
``advisory_lock_key`` to a test-local key, which exercises the whole mechanism
(dedicated thread, dedicated connection, ``lock_timeout``) without self-deadlock.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from clearinghouse_core import testing
from clearinghouse_core.testing import DbSessionLock, advisory_lock_key

#: XOR salts keeping each test's probe key distinct from the real session key *and*
#: from each other, so tests cannot queue behind the session or a sibling test.
_EXCLUSION_SALT = 0x2081
_TIMEOUT_SALT = 0x2082
_REENTRANCY_SALT = 0x2083
_RESET_SALT = 0x2084


def _test_db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


def _raw_dsn(url: str) -> str:
    """The plain ``postgresql://`` form ``asyncpg.connect`` accepts."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _salted_key(salt: int):
    """A stand-in for ``advisory_lock_key`` shifted off the real key space."""
    real = advisory_lock_key

    def _key(database_url: str) -> int:
        return real(database_url) ^ salt

    return _key


# --- unit tier: no database -------------------------------------------------


def test_advisory_lock_key_is_stable_and_database_scoped():
    """Same database name → same signed-bigint key, regardless of host or role."""
    a = advisory_lock_key("postgresql+asyncpg://u@localhost/usa_wa_test")
    assert a == advisory_lock_key("postgresql+asyncpg://other@elsewhere:6543/usa_wa_test")
    assert a != advisory_lock_key("postgresql+asyncpg://u@localhost/other_test")
    assert -(2**63) <= a < 2**63


def test_lock_timeout_defaults_and_reads_the_env(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_LOCK_TIMEOUT", raising=False)
    assert testing._lock_timeout_seconds() == 600.0
    monkeypatch.setenv("TEST_DATABASE_LOCK_TIMEOUT", "42")
    assert testing._lock_timeout_seconds() == 42.0


def test_release_without_acquire_is_a_noop():
    DbSessionLock().release()  # must not raise


def test_sessionfinish_releases_after_fixture_teardown(pytestconfig, monkeypatch):
    """The root ``conftest_db`` must release the lock — and only *after* the runner's
    own ``pytest_sessionfinish`` has torn down ``test_engine`` (whose CASCADE drops
    must still be under the lock), hence ``trylast``."""
    plugin = pytestconfig.pluginmanager.get_plugin("conftest_db")
    assert plugin is not None, "conftest_db is not registered as a plugin"
    hook = getattr(plugin, "pytest_sessionfinish", None)
    assert hook is not None, "conftest_db defines no pytest_sessionfinish; nothing releases"
    opts = getattr(hook, "pytest_impl", {})
    assert opts.get("trylast") is True, (
        "pytest_sessionfinish must be @pytest.hookimpl(trylast=True): conftest hooks run "
        "before builtins, and releasing before fixture teardown un-guards the final drops"
    )
    calls: list[bool] = []
    monkeypatch.setattr(plugin, "release_test_db_lock", lambda: calls.append(True))
    hook(session=None, exitstatus=0)
    assert calls == [True]


# --- db tier: real advisory locks against TEST_DATABASE_URL ------------------


@pytest.mark.db
async def test_the_lock_excludes_a_second_postgres_session(monkeypatch):
    """While held, a second Postgres session cannot take the key; after release it can."""
    url = _test_db_url()
    monkeypatch.setattr(testing, "advisory_lock_key", _salted_key(_EXCLUSION_SALT))
    key = testing.advisory_lock_key(url)
    lock = DbSessionLock()
    lock.acquire(url, timeout_seconds=30.0)
    probe = await asyncpg.connect(_raw_dsn(url))
    try:
        got = await probe.fetchval("SELECT pg_try_advisory_lock($1)", key)
        if got:  # bug path: undo before asserting so the key is not poisoned
            await probe.fetchval("SELECT pg_advisory_unlock($1)", key)
        assert got is False, "a concurrent session could take the lock while we hold it"
        assert lock.held
        lock.release()
        assert not lock.held
        got_after = await probe.fetchval("SELECT pg_try_advisory_lock($1)", key)
        assert got_after is True, "release did not free the lock for the next session"
        await probe.fetchval("SELECT pg_advisory_unlock($1)", key)
    finally:
        await probe.close()
        if lock.held:
            lock.release()


@pytest.mark.db
async def test_acquire_times_out_with_a_clear_message(monkeypatch):
    """A held key + finite timeout must fail loudly, naming the situation."""
    url = _test_db_url()
    monkeypatch.setattr(testing, "advisory_lock_key", _salted_key(_TIMEOUT_SALT))
    key = testing.advisory_lock_key(url)
    blocker = await asyncpg.connect(_raw_dsn(url))
    try:
        await blocker.execute("SELECT pg_advisory_lock($1)", key)
        lock = DbSessionLock()
        with pytest.raises(RuntimeError, match="another pytest session holds the test database"):
            lock.acquire(url, timeout_seconds=1.0)
        assert not lock.held
    finally:
        await blocker.close()  # disconnect releases the blocker's session lock


@pytest.mark.db
async def test_acquire_is_process_reentrant_and_single_database(monkeypatch):
    """A second acquire in the same process is a no-op — a second *connection* would
    self-deadlock against our own holder. A different database while held is a hard
    error, not a silent unprotected run."""
    url = _test_db_url()
    monkeypatch.setattr(testing, "advisory_lock_key", _salted_key(_REENTRANCY_SALT))
    lock = DbSessionLock()
    lock.acquire(url, timeout_seconds=30.0)
    try:
        lock.acquire(url, timeout_seconds=30.0)  # held → returns immediately, no deadlock
        assert lock.held
        other = make_url(url).set(database="other_db_test").render_as_string(hide_password=False)
        with pytest.raises(RuntimeError, match="different database"):
            lock.acquire(other, timeout_seconds=30.0)
    finally:
        lock.release()
    assert not lock.held
    lock.release()  # idempotent


async def test_the_session_fixtures_hold_the_advisory_lock(test_engine):
    """Pin the conftest wiring: any session that resolved ``test_engine`` holds the
    *real* key, so a concurrent session's fixtures queue instead of dropping our
    schemas mid-run."""
    url = _test_db_url()
    key = advisory_lock_key(url)
    probe = await asyncpg.connect(_raw_dsn(url))
    try:
        got = await probe.fetchval("SELECT pg_try_advisory_lock($1)", key)
        if got:
            await probe.fetchval("SELECT pg_advisory_unlock($1)", key)
        assert got is False, "test_engine ran without holding the session advisory lock"
    finally:
        await probe.close()


@pytest.mark.db
async def test_reset_migration_schemas_takes_the_process_lock(monkeypatch):
    """The second drop-everything route must share the same mechanism (#208): a
    session that never resolves ``test_engine`` acquires here and keeps holding, so
    the from-base migration replay that follows stays protected too."""
    url = _test_db_url()
    monkeypatch.setattr(testing, "advisory_lock_key", _salted_key(_RESET_SALT))
    fresh = DbSessionLock()
    monkeypatch.setattr(testing, "_SESSION_DB_LOCK", fresh)
    # Keep the reset away from the real schemas: this session's tables must survive.
    monkeypatch.setattr(testing, "declared_schemas", lambda: {"lock_probe_schema_208"})
    try:
        await testing.reset_migration_schemas(url)
        assert fresh.held, "reset_migration_schemas dropped schemas without the lock"
        await testing.reset_migration_schemas(url)  # already held → no second holder
        assert fresh.held
    finally:
        if fresh.held:
            fresh.release()
