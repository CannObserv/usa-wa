"""Archive-first results-cohort provider (#101, Phase B).

Turns the archived ``results.vote.wa.gov`` legislative cohorts into the House **Position** the
``house/`` builder consumes. :meth:`house_positions` yields ``{year: {LD: [HousePosition]}}``
re-parsed **offline** from each ``sos-legresults:<YYYYMMDD>`` :class:`RawPayload` (written by
:mod:`usa_wa_adapter_sos.results.harvest`) — no ``results.vote.wa.gov`` re-pull — and
:meth:`citation_events` yields the per-year attesting FetchEvent the positioned seat cites.
:meth:`senate_winners` (#106 A′) exposes the Senate half of the same wire — ballot attestation
for elected senators, not a structural position.

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
from usa_wa_adapter_sos.positions import HousePosition, SenateWinner
from usa_wa_adapter_sos.results.adapter import (
    LEGRESULTS_RESOURCE_PREFIX,
    election_year_from_resource_id,
)
from usa_wa_adapter_sos.results.normalize import build_house_positions, build_senate_winners
from usa_wa_adapter_sos.results.transport import parse_legislative_results

logger = get_logger(__name__)

HousePositionsByLd = dict[int, list[HousePosition]]
SenateWinnersByLd = dict[int, SenateWinner]


class SosResultsCohortProvider:
    """Archived ``results.vote.wa.gov`` legislative cohorts → the House-position map (#101)."""

    def __init__(self, *, session: AsyncSession, source_id: _ULID) -> None:
        self._session = session
        self._source_id = source_id
        self._positions: dict[int, HousePositionsByLd] | None = None
        self._senate: dict[int, SenateWinnersByLd] | None = None
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

    async def _wire_for(self, event_id: _ULID) -> bytes | None:
        """The archived CSV body of a FetchEvent, or ``None`` when the event is payload-less."""
        return (
            await self._session.execute(
                select(RawPayload.body).where(RawPayload.fetch_event_id == event_id)
            )
        ).scalar_one_or_none()

    async def house_positions(self) -> dict[int, HousePositionsByLd]:
        """``{election_year: {LD: [HousePosition]}}`` re-parsed offline from the archive."""
        if self._positions is not None:
            return self._positions
        positions: dict[int, HousePositionsByLd] = {}
        for year, (event_id, _fetched_at, _rid) in (await self.citation_events()).items():
            wire = await self._wire_for(event_id)
            positions[year] = build_house_positions(parse_legislative_results(wire)) if wire else {}
        self._positions = positions
        logger.info("sos_results_positions_loaded", extra={"years": len(positions)})
        return positions

    async def senate_winners(self) -> dict[int, SenateWinnersByLd]:
        """``{election_year: {LD: SenateWinner}}`` re-parsed offline from the archive (#106 A′).

        The Senate half of the same legislative-results wire the House map is built from — the
        winning candidacy per LD (attestation, not a structural position; see
        :class:`~usa_wa_adapter_sos.positions.SenateWinner`). Memoized like the sibling scans."""
        if self._senate is not None:
            return self._senate
        senate: dict[int, SenateWinnersByLd] = {}
        for year, (event_id, _fetched_at, _rid) in (await self.citation_events()).items():
            wire = await self._wire_for(event_id)
            senate[year] = build_senate_winners(parse_legislative_results(wire)) if wire else {}
        self._senate = senate
        logger.info("sos_results_senate_loaded", extra={"years": len(senate)})
        return senate
