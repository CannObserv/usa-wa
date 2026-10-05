"""Integration test for the Jurisdictional IA migration's seed shape.

Runs ``alembic upgrade head`` in-process against ``TEST_DATABASE_URL`` and
asserts the seeded row counts + sample shape. The containment graph the migration also
seeded (101 relationships over 11 types) is checked absent: #412 PR F dropped it.
Counterpart to the sync unit tests in :mod:`test_jurisdictional_seed` — both target the
regression class flagged in code-review round 2, finding #22.

Marked ``@pytest.mark.integration`` so the heavy alembic + asyncpg path stays
off the default test tier; run with ``uv run pytest -m integration``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from clearinghouse_core.testing import reset_migration_schemas

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


async def _fetch_counts(test_url: str) -> dict[str, int]:
    """Pull the row counts, a shape spot-check, and which dropped tables still exist."""
    engine = create_async_engine(test_url)
    queries = (
        ("jurisdiction_types", "SELECT COUNT(*) FROM clearinghouse_core.jurisdiction_types"),
        ("jurisdictions", "SELECT COUNT(*) FROM clearinghouse_core.jurisdictions"),
        (
            "usa_wa_present",
            "SELECT COUNT(*) FROM clearinghouse_core.jurisdictions WHERE slug = 'usa-wa'",
        ),
        (
            "dropped_still_present",
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'canonical'"
            " OR (table_schema = 'clearinghouse_core' AND table_name IN"
            " ('jurisdiction_relationships', 'jurisdiction_relationship_types',"
            " 'fetch_events', 'raw_payloads', 'citations'))",
        ),
    )
    counts: dict[str, int] = {}
    async with engine.connect() as conn:
        for label, query in queries:
            counts[label] = (await conn.execute(text(query))).scalar()
    await engine.dispose()
    return counts


# ``db`` as well as ``integration`` (#185): this test opens its own engine against
# ``TEST_DATABASE_URL`` instead of taking ``db_session``, so the conftest's
# fixture-closure sweep cannot see that it needs a database. Without the marker,
# ``pytest -m 'not db'`` would select it on a machine with none.
@pytest.mark.integration
@pytest.mark.db
def test_alembic_upgrade_head_seeds_expected_row_counts():
    """Wipe the test DB, run ``alembic upgrade head`` in-process, assert
    seeded row counts + shape spot-checks.

    Uses alembic's in-process API (``alembic.command.upgrade``) so the test
    runs in any process that has the project's package installed — no
    dependency on ``uv`` being on ``PATH``.
    """
    test_url = os.environ.get("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL not set")

    asyncio.run(reset_migration_schemas(test_url))

    # alembic/env.py resolves the URL as DATABASE_URL_OWNER → DATABASE_URL →
    # alembic.ini. Point DATABASE_URL at the test DB *and* clear the owner DSN
    # for the duration, so the upgrade can never fall through to the live DB.
    config = Config(str(ALEMBIC_INI))
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "DATABASE_URL_OWNER")}
    os.environ["DATABASE_URL"] = test_url
    os.environ.pop("DATABASE_URL_OWNER", None)
    try:
        command.upgrade(config, "head")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    counts = asyncio.run(_fetch_counts(test_url))
    assert counts["jurisdiction_types"] == 16, counts
    assert counts["jurisdictions"] == 101, counts
    assert counts["usa_wa_present"] == 1, counts
    assert counts["dropped_still_present"] == 0, counts
