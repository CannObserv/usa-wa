"""The test session must not be able to reach the production database (CR #191 finding 1).

``run_job()`` (#179) resolves ``DATABASE_URL`` and opens a session against it, with
``commit=True`` by default. In the shell the suite is actually run from, that variable
points at production — so a test that calls a migrated CLI's ``main()`` without first
installing :func:`~clearinghouse_core.testing.patch_job_runtime` would commit there.

That helper is opt-in, and #179b migrates ~46 more CLIs that each grow such a
``main()``. The root ``conftest.py`` therefore rewrites both production DSNs to an
unroutable sentinel for the whole session, turning a silent write-to-prod into a fast,
loud connection failure. These tests pin that guard — it is the kind of safety net that
is easy to delete during an unrelated refactor because nothing visibly depends on it.
"""

import os

import pytest
from sqlalchemy.engine import make_url

from clearinghouse_core.config import get_database_url

PRODUCTION_DB_NAME = "usa_wa"


def test_database_url_does_not_point_at_production():
    """The resolved DSN must not be the production database."""
    url = make_url(get_database_url())
    assert url.database != PRODUCTION_DB_NAME, (
        "DATABASE_URL resolves to production during tests; the conftest guard is gone. "
        "A run_job()-based main() called in a test would commit to production."
    )


def test_owner_url_does_not_point_at_production():
    """``alembic/env.py`` prefers ``DATABASE_URL_OWNER``, so it needs the same guard —
    otherwise a stray ``alembic upgrade`` in a test migrates production."""
    owner = os.environ.get("DATABASE_URL_OWNER")
    assert owner is not None, "DATABASE_URL_OWNER should be set (to the sentinel), not unset"
    assert make_url(owner).database != PRODUCTION_DB_NAME


def test_the_sentinel_is_unroutable_rather_than_merely_different():
    """Pointing the DSN somewhere *else* is not enough — it must go nowhere.

    A reachable substitute would let an accidental ``main()`` quietly succeed against
    whatever it happened to name. Port 1 on loopback refuses immediately, so the
    failure is fast and unmistakable.
    """
    url = make_url(get_database_url())
    assert url.host in {"127.0.0.1", "localhost"}
    assert url.port == 1


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="no test DSN configured — this is the unit tier (#185); the other three "
    "assertions above still pin the guard here",
)
def test_test_database_url_is_still_distinct_from_the_blocked_dsn():
    """The guard must not have clobbered the DSN the suite actually uses.

    The only one of these four that needs ``TEST_DATABASE_URL`` — it is a statement
    *about* that DSN. In the DB-free tier there is no DSN to make a statement about,
    so it skips rather than dragging the whole file back into the ``db`` tier.
    """
    test_url = make_url(os.environ["TEST_DATABASE_URL"])
    assert test_url.database is not None
    assert test_url.database.endswith("_test")
    assert test_url.database != PRODUCTION_DB_NAME
    assert (test_url.host, test_url.port) != (make_url(get_database_url()).host, 1)
