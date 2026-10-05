"""Roster provisioning (#225): the Source row and its coverage claims."""

from __future__ import annotations

from sqlalchemy import select

from clearinghouse_core.source_coverage import SourceCoverage
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.provisioning import get_or_create_roster_source

# The ``db`` marker is derived from the fixture closure (root conftest), not declared here.


class TestProvisioning:
    async def test_creates_the_source_once_and_seeds_coverage(self, db_session, usa_wa) -> None:
        first = await get_or_create_roster_source(db_session, usa_wa)
        second = await get_or_create_roster_source(db_session, usa_wa)
        assert first.id == second.id
        assert first.slug == ROSTER_SOURCE_SLUG
        assert first.retention_policy.name == "archival"
        claims = (
            (
                await db_session.execute(
                    select(SourceCoverage).where(SourceCoverage.source_id == first.id)
                )
            )
            .scalars()
            .all()
        )
        assert [c.range_start for c in claims] == ["1889"]
        assert [c.range_end for c in claims] == ["2025"]

    async def test_is_a_distinct_source_from_the_wsl_soap_row(self, db_session, usa_wa) -> None:
        """Same jurisdiction and target, different publisher and archive — the multi-source
        pattern. Sharing the WSL Source row would conflate a daily API with a biennial PDF."""
        roster = await get_or_create_roster_source(db_session, usa_wa)
        assert roster.slug != "usa_wa_legislature"
        assert roster.kind == "document"
