"""Archive-first results-cohort provider (#101, Phase B).

Turns the archived ``results.vote.wa.gov`` legislative cohorts into the House **Position** the
``house/`` builder consumes. :meth:`house_positions` yields ``{year: {LD: [HousePosition]}}``
re-parsed **offline** from each ``sos-legresults:<YYYYMMDD>`` :class:`RawPayload` (written by
:mod:`usa_wa_adapter_sos.results.harvest`) — no ``results.vote.wa.gov`` re-pull — and
:meth:`citation_events` yields the per-year attesting FetchEvent the positioned seat cites.

**"Latest" = latest payload-bearing event** — as in the filings provider: a forced re-pull
re-records a payload-less FetchEvent when the wire is byte-identical, so both reads join
``RawPayload`` and tie-break on the (monotonic ULID) event id (#82). Both scans are memoized.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from usa_wa_adapter_legislature.span_emit import CitationTarget
from usa_wa_adapter_sos.positions import HousePosition
from usa_wa_adapter_sos.results.adapter import (
    LEGRESULTS_RESOURCE_PREFIX,
    election_year_from_resource_id,
)
from usa_wa_adapter_sos.results.normalize import build_house_positions
from usa_wa_adapter_sos.results.transport import parse_legislative_results

logger = get_logger(__name__)

HousePositionsByLd = dict[int, list[HousePosition]]


class SosResultsCohortProvider:
    """Archived ``results.vote.wa.gov`` legislative cohorts → the House-position map (#101)."""

    def __init__(self, *, session: AsyncSession, source_id: _ULID) -> None:
        self._session = session
        self._source_id = source_id
        self._positions: dict[int, HousePositionsByLd] | None = None
        self._events: dict[int, CitationTarget] | None = None

    async def citation_events(self) -> dict[int, CitationTarget]:
        """``{election_year: (fetch_event_id, fetched_at, resource_id)}`` for each year's latest
        **payload-bearing** OK results cohort under the prefix — the per-biennium provenance the
        House-seat span emission cites (cite-every-biennium: the SOS results row is the Position
        authority). ``house_positions`` derives the wire body from the same events. Years with no
        archived cohort are omitted. Memoized — the builder calls this directly *and* via
        ``house_positions``, so the scan runs once."""
        if self._events is not None:
            return self._events
        rows = (
            await self._session.execute(
                select(FetchEvent.resource_id, FetchEvent.id, FetchEvent.fetched_at)
                .join(RawPayload, RawPayload.fetch_event_id == FetchEvent.id)
                .where(
                    FetchEvent.source_id == self._source_id,
                    FetchEvent.resource_id.like(f"{LEGRESULTS_RESOURCE_PREFIX}%"),
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

    async def house_positions(self) -> dict[int, HousePositionsByLd]:
        """``{election_year: {LD: [HousePosition]}}`` re-parsed offline from the archive."""
        if self._positions is not None:
            return self._positions
        positions: dict[int, HousePositionsByLd] = {}
        for year, (event_id, _fetched_at, _rid) in (await self.citation_events()).items():
            wire = (
                await self._session.execute(
                    select(RawPayload.body).where(RawPayload.fetch_event_id == event_id)
                )
            ).scalar_one_or_none()
            positions[year] = build_house_positions(parse_legislative_results(wire)) if wire else {}
        self._positions = positions
        logger.info("sos_results_positions_loaded", extra={"years": len(positions)})
        return positions
