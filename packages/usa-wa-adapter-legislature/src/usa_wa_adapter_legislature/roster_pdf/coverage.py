"""Declared coverage for the roster-PDF source (#225, the #180 pattern).

One claim, and the interesting thing about it is the **closed** ceiling. Every other feed this
repo reads is open-ended because it serves the current cycle; this one is a *snapshot of a
revision*. The 2025-06-05 edition covers through the 2025 session and cannot see a day past it,
so a 2026 succession does not appear until the next revision (historically ~biennial).

Declaring ``1889-2025`` rather than ``1889-`` is what keeps "is this source authoritative for the
current biennium?" answerable as data instead of as prose. It is not: operator events and the
WSL/SOS wires win at and above the WSL floor.
"""

from __future__ import annotations

from datetime import date

from clearinghouse_core.source_coverage import CoverageClaim, CoverageStatus, claim_for

#: The roster-PDF source slug -- matches :attr:`RosterPdfAdapter.source_slug` and its ``Source``.
ROSTER_SOURCE_SLUG = "usa_wa_legislature_roster"

#: Cohorts keyed by session year -- the dimension the document publishes on.
MEMBER_ROSTER = "member_roster"

ROSTER_COVERAGE: tuple[CoverageClaim, ...] = (
    CoverageClaim(
        source_slug=ROSTER_SOURCE_SLUG,
        dimension=MEMBER_ROSTER,
        range_start="1889",
        range_end="2025",
        status=CoverageStatus.verified,
        audited_at=date(2026, 8, 20),
        notes=(
            "Parsed live 2026-08-20 from the 2025-06-05 revision after the #252 corrections: "
            "8,584 member-year records across 1889-2025, 53 districts (all 49 current plus "
            "the historical 50-59), 0 rows unparsed. "
            "CLOSED ceiling on purpose: the document is a revision snapshot stamped June 2025 "
            "and lags the current biennium, so it is never authority there. Revisions run "
            "~biennially (18 since 1962), which is why this source is re-checked quarterly and "
            "never joins the daily refresh."
        ),
    ),
)

#: The roster's served span -- the harvest's bounds and the audit oracle's floor/ceiling.
ROSTER_SESSION_YEARS = claim_for(ROSTER_COVERAGE, MEMBER_ROSTER)
