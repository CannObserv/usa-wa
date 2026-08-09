"""PDC source coverage (#180) — the declared claim, and the harvest floor derived from it."""

from __future__ import annotations

from sqlalchemy import select

from clearinghouse_core.source_coverage import CoverageStatus, SourceCoverage
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction
from usa_wa_adapter_pdc.coverage import ELECTION_YEAR, PDC_COVERAGE, PDC_ELECTION_YEARS
from usa_wa_adapter_pdc.harvest_pdc import DEFAULT_ELECTION_FLOOR
from usa_wa_adapter_pdc.provisioning import get_or_create_source


def test_the_pdc_floor_is_declared_assumed():
    """``harvest_pdc`` called it "~2008 (the PDC campaign-finance dataset's coverage)" — an
    approximation nobody probed. ``assumed`` records that rather than dressing it as verified;
    an under-served year archives an *empty* cohort here, so the floor has never had to prove
    itself the way the SOS 500 forced the votewa bound to."""
    assert PDC_ELECTION_YEARS.status == CoverageStatus.assumed
    assert PDC_ELECTION_YEARS.dimension == ELECTION_YEAR
    assert PDC_ELECTION_YEARS.range_end is None  # open-ended — the dataset still publishes


def test_the_harvest_floor_is_the_claim():
    assert DEFAULT_ELECTION_FLOOR == PDC_ELECTION_YEARS.floor_year == 2008


async def test_provisioning_seeds_the_claim(db_session, usa_wa):
    jurisdiction = await resolve_jurisdiction(db_session)
    source = await get_or_create_source(db_session, jurisdiction)
    rows = (
        (
            await db_session.execute(
                select(SourceCoverage).where(SourceCoverage.source_id == source.id)
            )
        )
        .scalars()
        .all()
    )
    assert {(r.dimension, r.range_start, r.status) for r in rows} == {
        (c.dimension, c.range_start, c.status.value) for c in PDC_COVERAGE
    }
