"""SOS source coverage (#180) — the two SOS feeds' ranges, including the votewa gap.

This is where ``status = absent`` earns the schema. The fact that SOS retired the votewa
``ExportToExcel`` export to Power BI after the 2018 general — the finding that moved the House
Position seat onto the results source (#101) and that #169 turned into a CLI ceiling — existed
only as prose in ``docs/ARCHITECTURE.md`` L100-105. Prose cannot answer "does any source cover
2020?", so the answer was rediscovered by running a harvest into an HTTP 500.

Declared here as **two** claims on one dimension: ``verified`` 2008–2018 (the span the feed
serves) and ``absent`` 2020–onward (the span it does not, permanently). A known gap stated as a
fact is what distinguishes *no data* from *not looked* — and it is queryable, so the
availability questions #140 (the Era A pre-2002 House Position gap) and #135 (biennium-rollover
readiness) work around by hand now have a table to land in.

The results feed carries the contrast: ``verified`` 2008-onward, no gap. That asymmetry is the
entire reason the SOS target package holds two sources, and it is now data rather than an
inference from two docstrings.

Pure Python — imported by both harvest CLIs for their bounds, and projected into
``clearinghouse_core.source_coverage`` by :mod:`usa_wa_adapter_sos.provisioning`.
"""

from __future__ import annotations

from datetime import date

from clearinghouse_core.source_coverage import CoverageClaim, CoverageStatus, claim_for

#: The filings source slug (votewa ``ExportToExcel`` candidate filings).
SOS_FILINGS_SOURCE_SLUG = "usa_wa_sos"

#: The results source slug (``results.vote.wa.gov`` legislative election results).
SOS_RESULTS_SOURCE_SLUG = "usa_wa_sos_results"

#: Cohorts keyed by general-election year — the dimension both SOS feeds publish on.
ELECTION_YEAR = "election_year"

SOS_FILINGS_COVERAGE: tuple[CoverageClaim, ...] = (
    CoverageClaim(
        source_slug=SOS_FILINGS_SOURCE_SLUG,
        dimension=ELECTION_YEAR,
        range_start="2008",
        range_end="2018",
        status=CoverageStatus.verified,
        audited_at=date(2026, 8, 6),
        notes=(
            "Probed live 2026-08-06: electionDate=201811 returns 200 (consistent with the "
            "2026-07-18 audit at #101). Floor 2008 is the PDC winner floor this fills against "
            "— earlier years have no PDC cohort to join — rather than a probed feed bound. "
            "A CLOSED archive: the ceiling is the retirement below, not a moving edge."
        ),
    ),
    CoverageClaim(
        source_slug=SOS_FILINGS_SOURCE_SLUG,
        dimension=ELECTION_YEAR,
        range_start="2020",
        range_end=None,
        status=CoverageStatus.absent,
        audited_at=date(2026, 8, 6),
        notes=(
            "ABSENT, permanently: SOS retired the WhoFiled 'Export To Excel' control to Power "
            "BI after the 2018 general. electionDate=202011 returns HTTP 500 (probed live "
            "2026-08-06). Not an outage to retry — the export is gone. This is the claim that "
            "makes 'we have no 2020 filings' an answer rather than a silence; the House "
            "Position seat reads the results source for these years instead (#101)."
        ),
    ),
)

SOS_RESULTS_COVERAGE: tuple[CoverageClaim, ...] = (
    CoverageClaim(
        source_slug=SOS_RESULTS_SOURCE_SLUG,
        dimension=ELECTION_YEAR,
        range_start="2008",
        range_end=None,
        status=CoverageStatus.verified,
        audited_at=date(2026, 7, 24),
        notes=(
            "Probed at #106: every export index 2009→2025 exists, odd years included. Two odd "
            "years (2021, 2023) carry no Legislative CSV because no legislative race was held "
            "— an expected absence WITHIN the covered range, tallied by the harvest as "
            "cohorts_absent, not a coverage gap. Open-ended: the feed serves the current cycle, "
            "which is why the seat moved here from filings."
        ),
    ),
)

#: The filings feed's served span (2008–2018) — the #100 harvest's floor and, since #169, its
#: ceiling.
SOS_FILINGS_ELECTION_YEARS = claim_for(SOS_FILINGS_COVERAGE, ELECTION_YEAR)

#: The filings feed's known gap (2020–, permanent). The ``absent`` claim.
SOS_FILINGS_RETIRED = claim_for(SOS_FILINGS_COVERAGE, ELECTION_YEAR, status=CoverageStatus.absent)

#: The results feed's served span (2008–present) — the #101 harvest's floor.
SOS_RESULTS_ELECTION_YEARS = claim_for(SOS_RESULTS_COVERAGE, ELECTION_YEAR)
