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

import io

import pdfplumber
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
from usa_wa_adapter_legislature.roster_pdf.normalize import (
    PageWords,
    ParseReport,
    RosterRecord,
    Word,
    parse_district_pages_reporting,
)

logger = get_logger(__name__)


def extract_pages(wire: bytes) -> list[PageWords]:
    """Extract every page's word geometry from the archived PDF bytes.

    The whole document is handed to the parser; it bounds the *by district* section itself from
    the district banners. A hard-coded page range would silently truncate the tail — the
    districts run 1-60 historically, and the section sits at PDF pages 20-154 in the 2025
    revision, which is not where the printed page numbers say it is.
    """
    pages: list[PageWords] = []
    with pdfplumber.open(io.BytesIO(wire)) as pdf:
        for index, page in enumerate(pdf.pages):
            words = [
                Word(text=w["text"], x0=w["x0"], x1=w["x1"], top=w["top"])
                for w in page.extract_words()
            ]
            pages.append(PageWords(page_number=index + 1, width=page.width, words=words))
    return pages


class RosterCohortProvider:
    """Archived roster PDF → member-year records, re-parsed offline (#225)."""

    def __init__(self, *, session: AsyncSession, source_id: _ULID) -> None:
        self._session = session
        self._source_id = source_id
        self._report: ParseReport | None = None
        self._event: CitationTarget | None = None

    async def citation_event(self) -> CitationTarget | None:
        """``(fetch_event_id, fetched_at, resource_id)`` for the latest payload-bearing roster
        edition, or ``None`` when nothing is archived. One key per revision — the citation
        granularity settled on #219."""
        if self._event is not None:
            return self._event
        row = (
            await self._session.execute(
                select(FetchEvent.id, FetchEvent.fetched_at, FetchEvent.resource_id)
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
        return self._event

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
