"""The roster cohort's contribution to the sponsor span build (#228). Read-only.

Derives the WSL-joined identities' pre-1991 observations — the ``extra_observations`` the
sponsor builder merges so a crossing member's tenure emits as one span keyed at its true
start — plus the edition's citation target for the bienniums no sponsor wire attests.

**This is a standing input to every unrestricted sponsor build**, not a one-shot: a full
rebuild that omitted it would re-assert the shallow 1991-start span keys and recreate the
stranded rows the #97 collapse retires. The sponsor builder calls this itself
(``include_roster``); the daily restricted path never does (its cohort is the current
biennium's members, all post-1991). Returns ``([], None)`` when no roster archive exists —
a fresh database deepens nothing.

**Cost** (CR #94): each call re-parses the whole archived edition and re-resolves the
~6,200-record corpus — a few seconds, paid again by ``migrate_spans``' own internal build in
the same maintenance window. No timer pays it: every scheduled caller is restricted. Left
uncached deliberately — a memo keyed on the archive would have to invalidate on the
adjudication tables too, and the operator-only callers do not justify that.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import Source
from clearinghouse_domain_legislative.span_emit import CitationTarget
from clearinghouse_domain_legislative.tenure_spans import Observation
from usa_wa_adapter_legislature.coverage import WSL_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.backfill import load_seatings
from usa_wa_adapter_legislature.roster_pdf.cohort import RosterCohortProvider
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.identity import IDENTITY_WSL, resolve_identities
from usa_wa_adapter_legislature.roster_pdf.projector import build_pre1991_observations

logger = get_logger(__name__)


async def joined_pre1991_observations(
    session: AsyncSession,
) -> tuple[list[Observation], CitationTarget | None]:
    """``(observations, citation)`` for the WSL-joined identities' roster-era tenure.

    Lookup-only — neither Source row is created here; absence of either (or of the
    archive) means there is nothing to deepen yet.
    """
    roster_source = (
        await session.execute(select(Source).where(Source.slug == ROSTER_SOURCE_SLUG))
    ).scalar_one_or_none()
    wsl_source = (
        await session.execute(select(Source).where(Source.slug == WSL_SOURCE_SLUG))
    ).scalar_one_or_none()
    if roster_source is None or wsl_source is None:
        return [], None
    provider = RosterCohortProvider(session=session, source_id=roster_source.id)
    records = await provider.records()
    citation = await provider.citation_event()
    if not records or citation is None:
        return [], None
    seatings = await load_seatings(session, source_id=wsl_source.id)
    report = resolve_identities(records, seatings=seatings)
    projection = build_pre1991_observations(report.identities, records)
    joined = {i.wsl_member_id for i in report.identities if i.disposition == IDENTITY_WSL}
    observations = [o for o in projection.observations if o.member_id in joined]
    logger.info(
        "roster_deepening_derived",
        extra={"joined_members": len(joined), "observations": len(observations)},
    )
    return observations, citation
