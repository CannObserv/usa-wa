"""Archive-first SOS filing-cohort provider (#100/#101, Phase B).

Turns the archived votewa filing cohorts into the House **Position** the WSL+SOS builder
(:func:`usa_wa_adapter_sos.house.build.build_house_position_spans`) consumes:
:meth:`house_filings` yields ``{election_year: {LD: [HouseFiling]}}`` re-parsed **offline** from
each ``sos-whofiled:<YYYYMM>`` :class:`RawPayload` (written by :mod:`harvest_sos`) — no votewa
re-pull — and :meth:`citation_events` yields the per-year attesting FetchEvent the positioned
seat cites (SOS is the Position authority since #101).

**"Latest" = latest payload-bearing event** — as in :class:`PdcWinnerCohortProvider`: a forced
re-pull re-records a payload-less FetchEvent when the wire is byte-identical, so both reads join
``RawPayload`` and tie-break on the (monotonic ULID) event id. Both scans are memoized.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from usa_wa_adapter_legislature.span_emit import CitationTarget
from usa_wa_adapter_sos.filings.adapter import (
    WHOFILED_RESOURCE_PREFIX,
    election_year_from_resource_id,
)
from usa_wa_adapter_sos.filings.normalize import HouseFiling, build_house_filings
from usa_wa_adapter_sos.filings.transport import parse_whofiled

logger = get_logger(__name__)

HouseFilingsByLd = dict[int, list[HouseFiling]]


class SosFilingCohortProvider:
    """Archived votewa filing cohorts → the PDC House-position fallback (#100)."""

    def __init__(self, *, session: AsyncSession, source_id: _ULID) -> None:
        self._session = session
        self._source_id = source_id
        self._filings: dict[int, HouseFilingsByLd] | None = None
        self._events: dict[int, CitationTarget] | None = None

    async def citation_events(self) -> dict[int, CitationTarget]:
        """``{election_year: (fetch_event_id, fetched_at, resource_id)}`` for each year's latest
        **payload-bearing** OK filing cohort under the prefix — the per-biennium provenance the
        House-seat span emission cites (#101, cite-every-biennium: the SOS filing is the Position
        authority, so it is what the positioned seat traces to). ``house_filings`` derives the
        wire body from the same events. Years with no archived cohort are omitted. Memoized — the
        builder calls this directly *and* via ``house_filings``, so the scan runs once."""
        if self._events is not None:
            return self._events
        rows = (
            await self._session.execute(
                select(FetchEvent.resource_id, FetchEvent.id, FetchEvent.fetched_at)
                .join(RawPayload, RawPayload.fetch_event_id == FetchEvent.id)
                .where(
                    FetchEvent.source_id == self._source_id,
                    FetchEvent.resource_id.like(f"{WHOFILED_RESOURCE_PREFIX}%"),
                    FetchEvent.status == FetchStatus.ok,
                )
                .order_by(FetchEvent.fetched_at.desc(), FetchEvent.id.desc())  # newest first
            )
        ).all()
        events: dict[int, CitationTarget] = {}
        for resource_id, event_id, fetched_at in rows:
            year = election_year_from_resource_id(resource_id)
            events.setdefault(year, (event_id, fetched_at, resource_id))
        self._events = events
        return events

    async def house_filings(self) -> dict[int, HouseFilingsByLd]:
        """``{election_year: {LD: [HouseFiling]}}`` re-parsed offline from the archive."""
        if self._filings is not None:
            return self._filings
        filings: dict[int, HouseFilingsByLd] = {}
        for year, (event_id, _fetched_at, _rid) in (await self.citation_events()).items():
            wire = (
                await self._session.execute(
                    select(RawPayload.body).where(RawPayload.fetch_event_id == event_id)
                )
            ).scalar_one_or_none()
            filings[year] = build_house_filings(parse_whofiled(wire)) if wire else {}
        self._filings = filings
        logger.info("sos_filings_loaded", extra={"years": len(filings)})
        return filings
