"""PDC source coverage (#180) — what ``usa_wa_pdc`` serves, and how well we know it.

One dimension, one claim, declared ``assumed``: :mod:`harvest_pdc` recorded the floor as
"~2008 (the PDC campaign-finance dataset's coverage)" and nothing probed it. That is not a
defect of the harvest — the PDC SODA feed has no error path at the floor, a year with no data
simply archives an **empty** cohort — but it means the bound has never had to prove itself the
way the votewa HTTP 500 forced the SOS filings bound to. ``assumed`` records the difference
instead of letting an unchecked bound read as a checked one.

Pure Python, imported by :mod:`harvest_pdc` for its ``--from-year`` default and projected into
``clearinghouse_core.source_coverage`` by :mod:`usa_wa_adapter_pdc.provisioning`.
"""

from __future__ import annotations

from datetime import date

from clearinghouse_core.source_coverage import CoverageClaim, CoverageStatus, claim_for

#: The PDC source slug — matches the ``Source`` row ``provisioning`` get-or-creates.
PDC_SOURCE_SLUG = "usa_wa_pdc"

#: Seated winner cohorts keyed by general-election year (even seating years + odd specials,
#: #121).
ELECTION_YEAR = "election_year"

PDC_COVERAGE: tuple[CoverageClaim, ...] = (
    CoverageClaim(
        source_slug=PDC_SOURCE_SLUG,
        dimension=ELECTION_YEAR,
        range_start="2008",
        range_end=None,
        status=CoverageStatus.assumed,
        audited_at=date(2026, 7, 22),
        notes=(
            "ASSUMED, not verified: #79 recorded '~2008 (the PDC campaign-finance dataset's "
            "coverage)' with no probe. The SODA feed has no error path at the floor — an "
            "under-served year archives an EMPTY cohort (negative evidence), so a wrong floor "
            "is invisible. Open-ended: the dataset still publishes. audited_at is the date the "
            "claim was written down, not a probe date."
        ),
    ),
)
"""Every coverage claim the PDC source makes. Seeded by ``provisioning.get_or_create_source``."""

#: The election-year claim — the floor the #79 harvest sweeps from.
PDC_ELECTION_YEARS = claim_for(PDC_COVERAGE, ELECTION_YEAR, status=CoverageStatus.assumed)
