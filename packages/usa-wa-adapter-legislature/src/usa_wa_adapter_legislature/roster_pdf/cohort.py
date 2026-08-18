"""Archive-first roster cohort provider (#225, Phase B).

Re-parses the archived roster PDF **offline** from its :class:`RawPayload` — no ``leg.wa.gov``
re-pull. That split is load-bearing here more than anywhere else in the repo: the parser is the
riskiest component of this source and will be revised, and re-running a revised parser must never
mean re-fetching a 5.7MB document.

"Latest" is the latest **payload-bearing** OK event under the ``legroster:`` prefix, joined to
``RawPayload`` and tie-broken on the monotonic ULID event id — the #82 rule. A forced re-fetch
re-records a payload-less ``FetchEvent`` when the bytes are byte-identical, so ordering on
``FetchEvent`` alone would read the newest edition as an empty document.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from clearinghouse_domain_legislative.span_emit import CitationTarget
from usa_wa_adapter_legislature.roster_pdf.adapter import (
    ROSTER_RESOURCE_PREFIX,
    revision_from_resource_id,
)
from usa_wa_adapter_legislature.roster_pdf.extraction import extract_pages
from usa_wa_adapter_legislature.roster_pdf.normalize import (
    ParseReport,
    RosterRecord,
    parse_district_pages_reporting,
)

logger = get_logger(__name__)


class RosterCohortProvider:
    """Archived roster PDF → member-year records, re-parsed offline (#225)."""

    def __init__(self, *, session: AsyncSession, source_id: _ULID) -> None:
        self._session = session
        self._source_id = source_id
        self._report: ParseReport | None = None
        self._event: CitationTarget | None = None
        self._url: str | None = None

    async def citation_event(self) -> CitationTarget | None:
        """``(fetch_event_id, fetched_at, resource_id)`` for the latest payload-bearing roster
        edition, or ``None`` when nothing is archived. One key per revision — the citation
        granularity settled on #219."""
        if self._event is not None:
            return self._event
        row = (
            await self._session.execute(
                select(
                    FetchEvent.id,
                    FetchEvent.fetched_at,
                    FetchEvent.resource_id,
                    FetchEvent.url,
                )
                .join(RawPayload, RawPayload.fetch_event_id == FetchEvent.id)
                .where(
                    FetchEvent.source_id == self._source_id,
                    FetchEvent.resource_id.like(f"{ROSTER_RESOURCE_PREFIX}%"),
                    FetchEvent.status == FetchStatus.ok,
                )
                .order_by(FetchEvent.fetched_at.desc(), FetchEvent.id.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        self._event = (row.id, row.fetched_at, row.resource_id)
        self._url = row.url
        return self._event

    async def archived_url(self) -> str | None:
        """The URL the latest archived edition was actually fetched from, or ``None``.

        Rides :meth:`citation_event` rather than re-querying: the latest-payload-bearing rule
        (the ``RawPayload`` join plus the ULID tie-break, #82) is load-bearing and must have
        exactly one implementation — a second copy that drifted would cite a *different*
        edition than the one parsed (CR-5 finding 34). Callers need this because ``s4gf4suc``
        is a CMS media key the transport expects to rotate, so a citation pinned to the
        compiled-in URL dies while the archived bytes stay good."""
        await self.citation_event()
        return self._url

    async def report(self) -> ParseReport:
        """The parsed roster plus its unparsed tally. Memoized; empty when nothing is archived."""
        if self._report is not None:
            return self._report
        event = await self.citation_event()
        if event is None:
            logger.warning("roster_cohort_empty_archive")
            self._report = ParseReport(records=(), unparsed=())
            return self._report
        fetch_event_id, _, resource_id = event
        body = (
            await self._session.execute(
                select(RawPayload.body).where(RawPayload.fetch_event_id == fetch_event_id)
            )
        ).scalar_one()
        self._report = parse_district_pages_reporting(extract_pages(body))
        logger.info(
            "roster_cohort_parsed",
            extra={
                "revision": revision_from_resource_id(resource_id),
                "records": len(self._report.records),
                "unparsed": len(self._report.unparsed),
            },
        )
        return self._report

    async def records(self) -> tuple[RosterRecord, ...]:
        """Every member-year record in the archived edition."""
        return (await self.report()).records
