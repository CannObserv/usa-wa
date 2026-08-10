"""The cohort seam has implementers, and they keep satisfying it (#189).

A Protocol nothing conforms to is documentation, not a seam. These tests are the fitness
function that keeps `cohorts.py` honest in both directions:

1. every Protocol has at least one real implementer (so none is aspirational), and
2. renaming an accessor on a concrete provider fails here rather than silently unsubstituting
   two sources the architecture doc says are interchangeable.

`runtime_checkable` Protocols check method *presence*, not signatures — which is precisely
the regression worth catching. Signature drift is the type checker's job.

It lives in `scripts/tests/` rather than in any one package because it is the only place that
may import across every layer at once — the same reason `test_workspace_registries.py` and
`test_unit_tier.py` live here. A Layer-2 test importing a Layer-3 adapter would be the
inversion this issue exists to remove.

Pure `isinstance`: no DB, no wire. The providers are constructed with `None`s because nothing
here calls them.
"""

from __future__ import annotations

from clearinghouse_domain_legislative.cohorts import (
    ArchivedBienniumCohortProvider,
    AttestedCohortProvider,
    BienniumCohortProvider,
)


def test_sos_results_provider_is_an_attested_cohort_provider():
    """The results archive is citable through the seam."""
    from usa_wa_adapter_sos.results.cohort import SosResultsCohortProvider

    provider = SosResultsCohortProvider(session=None, source_id=None)
    assert isinstance(provider, AttestedCohortProvider)


def test_sos_filing_provider_is_an_attested_cohort_provider():
    """So is the filings archive — the second source behind the same fact."""
    from usa_wa_adapter_sos.filings.cohort import SosFilingCohortProvider

    assert isinstance(SosFilingCohortProvider(session=None, source_id=None), AttestedCohortProvider)


def test_pdc_winner_provider_is_an_attested_cohort_provider():
    """PDC's accessor was `house_events`; #189 gave it the seam's name too."""
    from usa_wa_adapter_pdc.cohort import PdcWinnerCohortProvider

    assert isinstance(PdcWinnerCohortProvider(session=None, source_id=None), AttestedCohortProvider)


def test_committee_roster_provider_is_an_archived_biennium_cohort_provider():
    """The reconcilers' input contract: a biennium cohort plus its archived domain."""
    from usa_wa_adapter_legislature.committees.cohort import CommitteeRosterCohortProvider

    provider = CommitteeRosterCohortProvider(None, session=None, source_id=None)
    assert isinstance(provider, ArchivedBienniumCohortProvider)


def test_meeting_provider_is_a_biennium_cohort_provider():
    """The meeting cohort satisfies the narrower Protocol — it has no archived-biennium scan,
    and the reconciler that consumes it never asks for one."""
    from usa_wa_adapter_legislature.meetings.cohort import MeetingCohortProvider

    provider = MeetingCohortProvider(None, session=None, source_id=None)
    assert isinstance(provider, BienniumCohortProvider)
    assert not isinstance(provider, ArchivedBienniumCohortProvider)


def test_house_position_seam_is_satisfied_by_both_sos_sources():
    """The claim `docs/ARCHITECTURE.md` makes — that which SOS archive supplies the Position
    "is the provider's concern, not the builder's" — was **not true** before #189: the results
    provider exposed `house_positions` and the filings provider exposed `house_filings`, so the
    two were not substitutable for one another under any name. Both now answer to the seam."""
    from usa_wa_adapter_sos.filings.cohort import SosFilingCohortProvider
    from usa_wa_adapter_sos.results.cohort import SosResultsCohortProvider
    from usa_wa_common.ballot import HousePositionCohortProvider

    for cls in (SosResultsCohortProvider, SosFilingCohortProvider):
        assert isinstance(cls(session=None, source_id=None), HousePositionCohortProvider), cls
