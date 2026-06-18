"""End-to-end integration test — live WSL + TEST_DATABASE_URL.

Run with ``uv run pytest -m integration``. Excluded from the default tier so
the offline suite stays hermetic.

The test invokes ``python -m usa_wa_adapter_legislature.refresh`` as a
subprocess against ``TEST_DATABASE_URL`` (which must already be at the
current migration head), then asserts the row counts via a fresh session.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.provenance import Citation, FetchEvent, RawPayload, Source
from clearinghouse_core.testing import assert_test_url_safety
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.sessions import LegislativeSession

pytestmark = pytest.mark.integration


async def _seed_jurisdiction(database_url: str) -> None:
    """Ensure the usa-wa Jurisdiction cache row exists (the refresh assumes it).

    Re-asserts the conftest URL safety guard before any destructive DML — this
    test opens its own engine and bypasses the savepointed ``db_session``
    fixture, so the module-level check at conftest import isn't sufficient
    on its own.
    """
    assert_test_url_safety(database_url)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM clearinghouse_core.citations"))
            await conn.execute(text("DELETE FROM canonical.organizations"))
            await conn.execute(text("DELETE FROM canonical.legislative_sessions"))
            await conn.execute(text("DELETE FROM clearinghouse_core.raw_payloads"))
            await conn.execute(text("DELETE FROM clearinghouse_core.fetch_events"))
            await conn.execute(text("DELETE FROM clearinghouse_core.sources"))
        async with AsyncSession(engine) as session:
            jur = (
                await session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-wa"))
            ).scalar_one_or_none()
            if jur is None:
                jtype = (
                    await session.execute(
                        select(JurisdictionType).where(JurisdictionType.slug == "state")
                    )
                ).scalar_one_or_none()
                if jtype is None:
                    jtype = JurisdictionType(slug="state", display_name="State")
                    session.add(jtype)
                    await session.flush()
                session.add(
                    Jurisdiction(
                        slug="usa-wa",
                        name="Washington State",
                        type_id=jtype.id,
                        recorded_at=datetime.now(UTC),
                    )
                )
                await session.commit()
    finally:
        await engine.dispose()


async def test_refresh_module_writes_full_anchor_chain_to_test_db():
    test_db_url = os.environ.get("TEST_DATABASE_URL")
    assert test_db_url, "TEST_DATABASE_URL must be set"
    assert_test_url_safety(test_db_url)

    # Run alembic upgrade head against the test DB so the schema matches.
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        env={**os.environ, "DATABASE_URL": test_db_url},
        capture_output=True,
    )
    await _seed_jurisdiction(test_db_url)

    result = subprocess.run(
        [sys.executable, "-m", "usa_wa_adapter_legislature.refresh"],
        env={**os.environ, "DATABASE_URL": test_db_url, "USA_WA_BIENNIUM": "2025-26"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"refresh failed: stdout={result.stdout} stderr={result.stderr}"
    assert "WSL refresh:" in result.stdout

    engine = create_async_engine(test_db_url)
    try:
        async with AsyncSession(engine) as session:
            sources = (await session.execute(select(Source))).scalars().all()
            assert len(sources) == 1

            fetch_events = (await session.execute(select(FetchEvent))).scalars().all()
            raw_payloads = (await session.execute(select(RawPayload))).scalars().all()
            assert len(fetch_events) == 1
            assert len(raw_payloads) == 1

            orgs = (await session.execute(select(Organization))).scalars().all()
            # 1 legislature + 2 chambers + ≥1 committee.
            legislature = [o for o in orgs if o.org_type == "legislature"]
            chambers = [o for o in orgs if o.org_type == "chamber"]
            committees = [o for o in orgs if o.org_type == "committee"]
            assert len(legislature) == 1
            assert len(chambers) == 2
            assert len(committees) >= 1

            sessions = (await session.execute(select(LegislativeSession))).scalars().all()
            biennium = [s for s in sessions if s.classification == "biennium"]
            regulars = [s for s in sessions if s.classification == "regular"]
            assert len(biennium) == 1
            assert len(regulars) == 2

            citations = (await session.execute(select(Citation))).scalars().all()
            assert len(citations) == len(committees)
    finally:
        await engine.dispose()
