"""Archive-first SOS filing-cohort provider (#100, Phase B).

Turns the archived votewa filing cohorts into the House-position **fallback** the PDC span
builder consumes: ``{election_year: {LD: [HouseFiling]}}`` re-parsed **offline** from each
``sos-whofiled:<YYYYMM>`` :class:`RawPayload` (written by :mod:`harvest_sos`) — no votewa
re-pull. :meth:`fallback_factory` closes over the loaded filings and returns a sync
``year -> PositionFallback`` the builder calls per cohort; a year with no archived cohort maps
to ``None`` (no fallback — those winners stay ``missing_position``).

**"Latest" = latest payload-bearing event** — as in :class:`PdcWinnerCohortProvider`: a forced
re-pull re-records a payload-less FetchEvent when the wire is byte-identical, so both reads join
``RawPayload`` and tie-break on the (monotonic ULID) event id. The scan is memoized.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID
from usa_wa_adapter_pdc.normalize.pdc_observations import PositionFallback

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from usa_wa_adapter_sos.adapter import WHOFILED_RESOURCE_PREFIX, election_year_from_resource_id
from usa_wa_adapter_sos.normalize.filings import HouseFiling, build_house_filings, position_for
from usa_wa_adapter_sos.transport import parse_whofiled

logger = get_logger(__name__)

HouseFilingsByLd = dict[int, list[HouseFiling]]


class SosFilingCohortProvider:
    """Archived votewa filing cohorts → the PDC House-position fallback (#100)."""

    def __init__(self, *, session: AsyncSession, source_id: _ULID) -> None:
        self._session = session
        self._source_id = source_id
        self._filings: dict[int, HouseFilingsByLd] | None = None

    async def _latest_events(self) -> dict[int, _ULID]:
        """Latest **payload-bearing** OK FetchEvent id per election year under the prefix."""
        rows = (
            await self._session.execute(
                select(FetchEvent.resource_id, FetchEvent.id)
                .join(RawPayload, RawPayload.fetch_event_id == FetchEvent.id)
                .where(
                    FetchEvent.source_id == self._source_id,
                    FetchEvent.resource_id.like(f"{WHOFILED_RESOURCE_PREFIX}%"),
                    FetchEvent.status == FetchStatus.ok,
                )
                .order_by(FetchEvent.fetched_at.desc(), FetchEvent.id.desc())  # newest first
            )
        ).all()
        latest: dict[int, _ULID] = {}
        for resource_id, event_id in rows:
            year = election_year_from_resource_id(resource_id)
            latest.setdefault(year, event_id)
        return latest

    async def house_filings(self) -> dict[int, HouseFilingsByLd]:
        """``{election_year: {LD: [HouseFiling]}}`` re-parsed offline from the archive."""
        if self._filings is not None:
            return self._filings
        filings: dict[int, HouseFilingsByLd] = {}
        for year, event_id in (await self._latest_events()).items():
            wire = (
                await self._session.execute(
                    select(RawPayload.body).where(RawPayload.fetch_event_id == event_id)
                )
            ).scalar_one_or_none()
            filings[year] = build_house_filings(parse_whofiled(wire)) if wire else {}
        self._filings = filings
        logger.info("sos_filings_loaded", extra={"years": len(filings)})
        return filings

    async def fallback_factory(self) -> Callable[[int], PositionFallback | None]:
        """A sync ``election_year -> PositionFallback | None`` closed over the loaded archive —
        the callable :func:`~usa_wa_adapter_pdc.build_pdc_spans.build_pdc_spans` injects (#100)."""
        filings = await self.house_filings()

        def factory(year: int) -> PositionFallback | None:
            by_ld = filings.get(year)
            if not by_ld:
                return None

            def fallback(ld: int, folded_last: str, party_slug: str | None) -> str | None:
                return position_for(by_ld, ld, folded_last, party_slug)

            return fallback

        return factory
