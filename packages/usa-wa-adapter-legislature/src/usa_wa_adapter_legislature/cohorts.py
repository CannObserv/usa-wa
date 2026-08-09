"""Cohort-provider factories — the WSL adapter's public seam (#189, AR-14).

A consumer that wants *archived WSL committee data* should ask for a provider, not build one
out of a SOAP client it had to import from this package's `transport`. Before this module,
`usa_wa_sync_powermap` — a **Layer-4 deployment** package — imported
`usa_wa_adapter_legislature.transport.WSLClient` in five modules and constructed the providers
itself, so the PM sync sidecar made live SOAP calls to the Washington State Legislature.

These factories own the transport choice (which WSL *service* backs which cohort) and the
provenance `Source` lookup, and hand back something typed by
:mod:`clearinghouse_domain_legislative.cohorts` — so a caller depends on
"a provider of archived committee rosters" and nothing more.

The `Source` lookup is **read-only** by design: the sidecar's reconcilers never commit (PM is
the authority for the columns they mirror), so they have no business provisioning a `Source`
row. A `source_id` of ``None`` — a DB that has never run a WSL pull — is not an error: the
providers are archive-*first*, not archive-only, so they simply fall back to a live pull. Pass
an explicit ``source_id`` to override (the rename-chain emitter get-or-creates its Source
because it writes provenance).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.provenance import Source
from usa_wa_adapter_legislature.committee_member_cohort import CommitteeMemberCohortProvider
from usa_wa_adapter_legislature.committee_roster_cohort import CommitteeRosterCohortProvider
from usa_wa_adapter_legislature.meetings.cohort import MeetingCohortProvider
from usa_wa_adapter_legislature.sponsor_cohort import SponsorRosterCohortProvider
from usa_wa_adapter_legislature.transport import WSLClient

#: The WSL provenance `Source.slug` every cohort here is archived under. Equal to the
#: `Organization.source` producer string by convention.
WSL_SOURCE_SLUG = "usa_wa_legislature"


async def resolve_source_id(session: AsyncSession) -> _ULID | None:
    """The WSL `Source.id`, or ``None`` when no WSL pull has ever run against this DB.

    A read-only lookup, never a get-or-create: the callers are read paths that must not
    provision provenance."""
    return (
        await session.execute(select(Source.id).where(Source.slug == WSL_SOURCE_SLUG))
    ).scalar_one_or_none()


async def committee_roster_provider(
    session: AsyncSession, *, source_id: _ULID | None = None
) -> CommitteeRosterCohortProvider:
    """An archive-first provider over the ``committees-roster:<biennium>`` archive.

    Satisfies `clearinghouse_domain_legislative.cohorts.ArchivedBienniumCohortProvider`:
    `cohort(biennium)`, `roster_records(biennium)` and `archived_bienniums()`. A closed prior
    biennium is a cache hit on the archive `harvest_committees` wrote, not a fresh
    ``GetCommittees`` pull."""
    if source_id is None:
        source_id = await resolve_source_id(session)
    return CommitteeRosterCohortProvider(
        WSLClient("CommitteeService"), session=session, source_id=source_id
    )


def sponsor_roster_provider(
    session: AsyncSession, *, source_id: _ULID
) -> SponsorRosterCohortProvider:
    """An archive-first provider over the ``sponsors:<biennium>`` roster archive.

    ``source_id`` is required, not looked up: every caller is a Phase-B span builder that has
    already get-or-created the WSL `Source` (it writes provenance), and a silent ``None`` here
    would turn an archive-derived build into a full live re-sweep of thirty years of WSL."""
    return SponsorRosterCohortProvider(
        WSLClient("SponsorService"), session=session, source_id=source_id
    )


def committee_member_provider(
    session: AsyncSession, *, source_id: _ULID
) -> CommitteeMemberCohortProvider:
    """An archive-first provider over the ``committee-members-hist:`` roster archive (#82).

    ``source_id`` required, for the same reason as :func:`sponsor_roster_provider`."""
    return CommitteeMemberCohortProvider(
        WSLClient("CommitteeService"), session=session, source_id=source_id
    )


async def committee_meeting_provider(
    session: AsyncSession, *, source_id: _ULID | None = None
) -> MeetingCohortProvider:
    """An archive-first provider over the ``committee-meetings:<begin>:<end>`` archive.

    Satisfies `clearinghouse_domain_legislative.cohorts.BienniumCohortProvider`. A closed
    window (immutable, ~1.5 MB) is re-parsed offline rather than re-pulled from WSL."""
    if source_id is None:
        source_id = await resolve_source_id(session)
    return MeetingCohortProvider(
        WSLClient("CommitteeMeetingService"), session=session, source_id=source_id
    )
