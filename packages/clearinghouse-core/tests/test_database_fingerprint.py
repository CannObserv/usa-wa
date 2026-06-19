"""Integration test for the connection fingerprint helper.

Marked ``@pytest.mark.integration`` so the asyncpg round-trip stays off the
default tier; run with ``uv run pytest -m integration``.
"""

from __future__ import annotations

import pytest

from clearinghouse_core.database import fetch_connection_fingerprint


@pytest.mark.integration
async def test_fetch_connection_fingerprint_reports_user_and_database(db_session):
    """Returns the live ``(current_user, current_database)`` for the session."""
    db_user, db_name = await fetch_connection_fingerprint(db_session)
    assert db_user, "expected a non-empty current_user"
    assert db_name.endswith("_test"), f"test session should be on a *_test DB, got {db_name!r}"
