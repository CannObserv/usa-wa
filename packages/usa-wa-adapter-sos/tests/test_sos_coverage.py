"""SOS source coverage (#180) — the two SOS feeds' claims, incl. the votewa 2020+ gap."""

from __future__ import annotations

from sqlalchemy import select

from clearinghouse_core.source_coverage import CoverageStatus, SourceCoverage, known_gaps
from usa_wa_adapter_legislature.coverage import SPONSOR_ROSTER_COVERAGE
from usa_wa_adapter_sos.coverage import (
    SOS_FILINGS_COVERAGE,
    SOS_FILINGS_ELECTION_YEARS,
    SOS_FILINGS_RETIRED,
    SOS_RESULTS_COVERAGE,
    SOS_RESULTS_ELECTION_YEARS,
)
from usa_wa_adapter_sos.filings.harvest import DEFAULT_ELECTION_CEILING, DEFAULT_ELECTION_FLOOR
from usa_wa_adapter_sos.house_corroboration import SWEEP_FLOOR_YEAR
from usa_wa_adapter_sos.provisioning import get_or_create_results_source, get_or_create_source
from usa_wa_adapter_sos.results.harvest import DEFAULT_ELECTION_FLOOR as RESULTS_FLOOR
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


def test_the_filings_bounds_are_the_claim():
    """#169's ceiling constant and the harvest floor are two ends of one claim, not two
    independent constants that have to be kept in step by hand."""
    assert DEFAULT_ELECTION_FLOOR == SOS_FILINGS_ELECTION_YEARS.floor_year == 2008
    assert DEFAULT_ELECTION_CEILING == SOS_FILINGS_ELECTION_YEARS.ceiling_year == 2018


def test_the_results_floor_is_the_claim():
    assert RESULTS_FLOOR == SOS_RESULTS_ELECTION_YEARS.floor_year == 2008


def test_the_house_sweep_floor_comes_from_the_wsl_claim_not_a_local_copy():
    """``SWEEP_FLOOR_YEAR = 1991`` was declared identically in this package and in
    ``usa_wa_adapter_legislature.succession_invariants``, both meaning "the WSL sponsor-archive
    floor". The duplicate is gone: this reads the WSL claim, so a re-audit of that feed moves
    both sweeps at once."""
    assert SWEEP_FLOOR_YEAR == SPONSOR_ROSTER_COVERAGE.floor_year == 1991


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
