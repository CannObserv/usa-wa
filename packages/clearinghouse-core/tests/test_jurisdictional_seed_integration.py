"""Integration test for the Jurisdictional IA migration's seed shape.

Runs ``alembic upgrade head`` in-process against ``TEST_DATABASE_URL`` and
asserts the seeded row counts + sample shape. Counterpart to the sync unit
tests in :mod:`test_jurisdictional_seed` — both target the regression class
flagged in code-review round 2, finding #22.

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

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


async def _wipe(test_url: str) -> None:
    """Drop alembic_version + the two declared schemas so the upgrade runs
    against a known-clean state."""
    engine = create_async_engine(test_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
        await conn.execute(text("DROP SCHEMA IF EXISTS clearinghouse_core CASCADE"))
        await conn.execute(text("DROP SCHEMA IF EXISTS canonical CASCADE"))
    await engine.dispose()


async def _fetch_counts(test_url: str) -> dict[str, int]:
    """Pull the row counts + two shape spot-checks that the assertions below
    consume."""
    engine = create_async_engine(test_url)
    queries = (
        ("jurisdiction_types", "SELECT COUNT(*) FROM clearinghouse_core.jurisdiction_types"),
        (
            "jurisdiction_relationship_types",
            "SELECT COUNT(*) FROM clearinghouse_core.jurisdiction_relationship_types",
        ),
        ("jurisdictions", "SELECT COUNT(*) FROM clearinghouse_core.jurisdictions"),
        (
            "jurisdiction_relationships",
            "SELECT COUNT(*) FROM clearinghouse_core.jurisdiction_relationships",
        ),
        (
            "usa_wa_present",
            "SELECT COUNT(*) FROM clearinghouse_core.jurisdictions WHERE slug = 'usa-wa'",
        ),
        (
            "wa_contained_by_usa",
            "SELECT COUNT(*) FROM clearinghouse_core.jurisdiction_relationships jr"
            " JOIN clearinghouse_core.jurisdictions sj"
            " ON jr.subject_jurisdiction_id = sj.id"
            " JOIN clearinghouse_core.jurisdictions oj"
            " ON jr.object_jurisdiction_id = oj.id"
            " WHERE sj.slug = 'usa-wa' AND oj.slug = 'usa'",
        ),
    )
    counts: dict[str, int] = {}
    async with engine.connect() as conn:
        for label, query in queries:
            counts[label] = (await conn.execute(text(query))).scalar()
    await engine.dispose()
    return counts


@pytest.mark.integration
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

    asyncio.run(_wipe(test_url))

    # alembic/env.py reads DATABASE_URL from os.environ first, then falls back
    # to alembic.ini's sqlalchemy.url. Temporarily override DATABASE_URL so the
    # upgrade targets TEST_DATABASE_URL instead of the live DB.
    config = Config(str(ALEMBIC_INI))
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url
    try:
        command.upgrade(config, "head")
    finally:
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url

    counts = asyncio.run(_fetch_counts(test_url))
    assert counts["jurisdiction_types"] == 16, counts
    assert counts["jurisdiction_relationship_types"] == 11, counts
    assert counts["jurisdictions"] == 101, counts
    assert counts["jurisdiction_relationships"] == 101, counts
    assert counts["usa_wa_present"] == 1, counts
    assert counts["wa_contained_by_usa"] == 1, counts
