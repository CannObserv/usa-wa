"""Integration test for the connection fingerprint helper.

Marked ``@pytest.mark.integration`` so the asyncpg round-trip stays off the
default tier; run with ``uv run pytest -m integration``.
"""

from __future__ import annotations

import pytest

from clearinghouse_core import database
from clearinghouse_core.database import (
    dispose_engine,
    fetch_connection_fingerprint,
    reset_engine,
)


@pytest.mark.integration
async def test_fetch_connection_fingerprint_reports_user_and_database(db_session):
    """Returns the live ``(current_user, current_database)`` for the session."""
    db_user, db_name = await fetch_connection_fingerprint(db_session)
    assert db_user, "expected a non-empty current_user"
    assert db_name.endswith("_test"), f"test session should be on a *_test DB, got {db_name!r}"


async def test_dispose_engine_is_a_noop_when_no_engine_was_created():
    """The job harness disposes unconditionally at the end of every run, including a
    run that failed before touching the database — so this must not resolve
    ``DATABASE_URL`` (and raise) just to tear down."""
    reset_engine()
    await dispose_engine()  # must not raise


async def test_dispose_engine_clears_the_cached_engine(monkeypatch):
    """Disposal also clears the module cache, so a later ``get_engine()`` builds a
    fresh engine rather than handing back a disposed one."""
    disposed: list[bool] = []

    class _Engine:
        async def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(database, "_engine", _Engine())
    monkeypatch.setattr(database, "_session_factory", object())

    await dispose_engine()

    assert disposed == [True]
    assert database._engine is None
    assert database._session_factory is None
