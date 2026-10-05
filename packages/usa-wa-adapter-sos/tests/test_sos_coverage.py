"""SOS source coverage (#180) — the two SOS feeds' claims, incl. the votewa 2020+ gap."""

from __future__ import annotations

from sqlalchemy import select

from clearinghouse_core.source_coverage import CoverageStatus, SourceCoverage, known_gaps
from usa_wa_adapter_sos.coverage import (
    SOS_FILINGS_COVERAGE,
    SOS_FILINGS_ELECTION_YEARS,
    SOS_FILINGS_RETIRED,
    SOS_RESULTS_COVERAGE,
    SOS_RESULTS_ELECTION_YEARS,
)
from usa_wa_adapter_sos.provisioning import get_or_create_results_source, get_or_create_source
from usa_wa_common.jurisdiction import resolve_jurisdiction


def test_the_votewa_retirement_is_an_absent_claim_not_prose():
    """The load-bearing fact that votewa retired ``ExportToExcel`` to Power BI after the 2018
    general lived only as a sentence in docs/ARCHITECTURE.md. It is now a row: the served span
    is ``verified`` 2008–2018, and 2020-onward is ``absent`` — a known gap stated as a fact,
    which is what lets a builder distinguish "no data" from "not looked"."""
    assert SOS_FILINGS_ELECTION_YEARS.status == CoverageStatus.verified
    assert (SOS_FILINGS_ELECTION_YEARS.range_start, SOS_FILINGS_ELECTION_YEARS.range_end) == (
        "2008",
        "2018",
    )
    assert SOS_FILINGS_RETIRED.status == CoverageStatus.absent
    assert SOS_FILINGS_RETIRED.range_start == "2020"
    assert SOS_FILINGS_RETIRED.range_end is None  # permanent — a closed archive, not an outage
    assert known_gaps(SOS_FILINGS_COVERAGE) == (SOS_FILINGS_RETIRED,)


def test_the_results_feed_has_no_gap():
    """The second source exists precisely because it covers what filings cannot — the contrast
    the coverage table makes queryable instead of inferable from two docstrings."""
    assert SOS_RESULTS_ELECTION_YEARS.status == CoverageStatus.verified
    assert SOS_RESULTS_ELECTION_YEARS.range_end is None
    assert known_gaps(SOS_RESULTS_COVERAGE) == ()


async def test_provisioning_seeds_both_sos_feeds(db_session, usa_wa):
    jurisdiction = await resolve_jurisdiction(db_session)
    filings = await get_or_create_source(db_session, jurisdiction)
    results = await get_or_create_results_source(db_session, jurisdiction)

    async def _rows(source):
        return (
            (
                await db_session.execute(
                    select(SourceCoverage).where(SourceCoverage.source_id == source.id)
                )
            )
            .scalars()
            .all()
        )

    assert {(r.range_start, r.status) for r in await _rows(filings)} == {
        ("2008", "verified"),
        ("2020", "absent"),
    }
    assert {(r.range_start, r.status) for r in await _rows(results)} == {("2008", "verified")}
