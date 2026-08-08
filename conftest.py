"""Workspace-root pytest base — deliberately database-free (#185).

Two tiers share this file:

* ``pytest -m 'not db'`` — the unit tier. Normalizers, projectors, span arithmetic,
  calendar functions, the systemd and registry guards. Runs with **no** database
  environment at all, on any machine.
* ``pytest`` — the full run, which additionally resolves the fixtures in
  :file:`conftest_db.py`.

Nothing here may touch a database or import a jurisdiction package. The DB fixtures
live in :file:`conftest_db.py` (registered below as a plugin) and fail *lazily*, at
fixture resolution — so a missing ``TEST_DATABASE_URL`` stops the tests that need one
instead of stopping collection for the whole workspace, which is what it used to do.

Layer discipline: the autouse WSL rate-limit fixture used to live here, which made
every Layer-1 ``clearinghouse-core`` test import the Layer-3 SOAP transport. It now
lives in :file:`packages/usa-wa-adapter-legislature/tests/conftest.py`, the only
package whose tests drive a real ``WSLClient``. ``scripts/tests/test_unit_tier.py``
keeps it from creeping back.
"""

import os
from collections.abc import Iterable
from typing import Any

import pytest

from clearinghouse_core.config import get_settings
from clearinghouse_core.testing import assert_test_url_safety

#: Fixtures that hand a test a live connection. Requesting one — directly or through
#: any fixture that requests one, since pytest resolves the whole closure — is what
#: makes a test a database test.
DB_FIXTURES = frozenset({"test_engine", "db_session"})

# Assert the test DSN's safety *before* the production guard below rewrites
# ``DATABASE_URL``: two of ``assert_test_url_safety``'s three belts compare the test
# DSN against the real production one (same URL, same role), and comparing it against
# the sentinel would quietly pass anything. When ``TEST_DATABASE_URL`` is unset there
# is nothing to vet — ``test_engine`` raises instead, at resolution time.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    assert_test_url_safety(TEST_DATABASE_URL)

# --- Fail closed on the production DSN (CR #191 finding 1) -------------------
#
# ``run_job()`` (#179) resolves ``DATABASE_URL`` via ``get_database_url()`` and opens a
# session against it. In the shell the suite is actually run from — ``export $(cat
# /etc/usa-wa/.env .env | xargs)`` — that resolves to the **production** database, and
# with the harness's default ``commit=True`` an accidental ``main()`` call in a test
# would commit there. ``clearinghouse_core.testing.patch_job_runtime`` prevents it, but
# it is opt-in, and #179b migrates ~46 more CLIs that each grow such a ``main()``.
#
# So neutralize the DSN for the whole test session: point it at an unroutable sentinel.
# Any code path that reaches for the production database during tests now fails fast and
# loudly instead of silently succeeding against prod.
#
# Unconditional, and in particular not contingent on ``TEST_DATABASE_URL`` being set:
# the unit tier is exactly the run most likely to happen in a shell carrying the
# production env and no test database.
#
# Why not simply set it to TEST_DATABASE_URL: ``assert_test_url_safety`` *requires* the
# two to differ (belt 1), so aliasing them would trip the very guard this reinforces.
#
# ``DATABASE_URL_OWNER`` gets the same treatment — ``alembic/env.py`` prefers it over
# ``DATABASE_URL``, so leaving it live would let a stray ``alembic upgrade`` migrate
# production.
#
# Escape hatch: the integration tests that legitimately drive a subprocess against the
# test DB build an explicit child env (see ``test_refresh_e2e.py``), which overrides
# this; and ``patch_job_runtime`` bypasses DSN resolution entirely.
BLOCKED_DATABASE_URL = "postgresql+asyncpg://blocked:blocked@127.0.0.1:1/blocked_by_conftest"
os.environ["DATABASE_URL"] = BLOCKED_DATABASE_URL
os.environ["DATABASE_URL_OWNER"] = BLOCKED_DATABASE_URL
# Settings is @lru_cache'd and snapshots the environment at first construction, so drop
# any instance built before this point.
get_settings.cache_clear()

#: The database fixtures. A plugin rather than more of this file so the tier boundary
#: is legible: everything above this line is what ``-m 'not db'`` relies on.
pytest_plugins = ("conftest_db",)


def pytest_collection_modifyitems(items: Iterable[Any]) -> None:
    """Mark every test that resolves a shared DB fixture with ``db``.

    102 of 148 test files need PostgreSQL. Marking them by hand would be 102 edits
    that a 103rd test can forget; deriving the marker from the fixture closure cannot
    drift, and covers indirection (the API ``client`` fixture requests ``db_session``,
    so its tests are database tests without saying so).

    Tests that bypass the fixtures and open their own engine are invisible here and
    carry ``@pytest.mark.db`` in the file — ``scripts/tests/test_unit_tier.py`` checks
    that they do.
    """
    for item in items:
        if DB_FIXTURES.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.db)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"
