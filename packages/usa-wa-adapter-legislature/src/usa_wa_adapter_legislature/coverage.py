"""WSL source coverage (#180) — what ``usa_wa_legislature`` serves, and how we know.

The single declaration behind every WSL-derived floor in the workspace. Before this,
``DEFAULT_HISTORY_FLOOR = "1991-92"`` lived in :mod:`sponsors.probe_identity`,
``DEFAULT_MEMBERSHIP_FLOOR = "1999-00"`` in :mod:`membership.harvest`, and
``SWEEP_FLOOR_YEAR = 1991`` — the *same* fact as the first, in year form — was declared
independently in :mod:`operators.invariants` **and** in
:mod:`usa_wa_facts_seats.house_corroboration`. Each was a coverage audit's conclusion
recorded as a comment, so nothing could answer "which years rest on this archive, and when
was that last checked?"

**One source, two dimensions.** WSL serves ``GetSponsors`` back to 1991-92 but
``GetCommitteeMembers`` only to ~1999-00. A per-*source* floor cannot express that, which is
why :class:`~clearinghouse_core.source_coverage.CoverageClaim` keys on a ``dimension``.

The claims are pure Python, so importing this costs nothing and a CLI's ``--help`` still
works with no database; :func:`~clearinghouse_core.source_coverage.seed_source_coverage`
projects the same objects into ``clearinghouse_core.source_coverage`` from
:mod:`usa_wa_adapter_legislature.provisioning`. See that module for the seam's rationale.
"""

from __future__ import annotations

from datetime import date

from clearinghouse_core.source_coverage import CoverageClaim, CoverageStatus, claim_for

#: The WSL SOAP source slug — matches the ``Source`` row ``provisioning`` get-or-creates.
WSL_SOURCE_SLUG = "usa_wa_legislature"

#: ``SponsorService.GetSponsors(biennium)`` — the member roster archive (#77).
SPONSOR_ROSTER = "sponsor_roster"

#: ``CommitteeService.GetCommitteeMembers(biennium, agency, name)`` — the committee roster
#: archive (#82). A *different* floor from the sponsor roster, on the same source.
COMMITTEE_MEMBERSHIP = "committee_membership"

WSL_COVERAGE: tuple[CoverageClaim, ...] = (
    CoverageClaim(
        source_slug=WSL_SOURCE_SLUG,
        dimension=SPONSOR_ROSTER,
        range_start="1991-92",
        range_end=None,
        status=CoverageStatus.verified,
        audited_at=date(2026, 7, 8),
        notes=(
            "Probed live (#81): 1991-92 returns a roster, 1989-90 faults. Open-ended — the op "
            "serves the current biennium. The #77 harvest and the #78 span builders rest on "
            "this bound, as do the year-keyed succession sweeps via floor_year."
        ),
    ),
    CoverageClaim(
        source_slug=WSL_SOURCE_SLUG,
        dimension=COMMITTEE_MEMBERSHIP,
        range_start="1999-00",
        range_end=None,
        status=CoverageStatus.assumed,
        audited_at=date(2026, 7, 22),
        notes=(
            "ASSUMED, not verified: #82 recorded the floor as '~1999-00 — below it WSL's "
            "truncated old committee names don't resolve and the op faults', with no dated "
            "probe. The transport swallows that fault to an empty roster, so a wrong floor "
            "here fails silently (the sweep passes through clean) rather than loudly — which "
            "is exactly why it is worth re-auditing to promote to verified. audited_at is the "
            "date the claim was written down, not a probe date."
        ),
    ),
)
"""Every coverage claim the WSL source makes. Seeded by ``provisioning.get_or_create_source``."""

#: The sponsor-roster claim — the floor for the #77 harvest, the #81 identity probe, and (via
#: :attr:`~clearinghouse_core.source_coverage.CoverageClaim.floor_year`) both odd-year
#: succession sweeps.
SPONSOR_ROSTER_COVERAGE = claim_for(WSL_COVERAGE, SPONSOR_ROSTER)

#: The committee-membership claim — the floor for the #82 harvest.
COMMITTEE_MEMBERSHIP_COVERAGE = claim_for(WSL_COVERAGE, COMMITTEE_MEMBERSHIP)
