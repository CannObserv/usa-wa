"""Archive-first PDC winner-cohort provider (#79, Phase B).

Turns the archived PDC winner cohorts into the span builder's inputs:

- :meth:`house_cohorts` / :meth:`senate_cohorts` — ``{election_year: [winner rows]}``,
  re-parsed **offline** from each ``house-winners:<Y>`` / ``senate-winners:<Y>``
  :class:`RawPayload` (written by the Phase A harvest, :mod:`harvest_pdc`) — no SODA re-pull.
- :meth:`house_events` / :meth:`senate_events` — ``{election_year: (fetch_event_id,
  fetched_at, resource_id)}``, the per-cohort provenance each House Position span cites.

**"Latest" = latest payload-bearing event.** As in the committee provider (usa-wa#82 CR): the
runner re-records a FetchEvent on a forced re-pull but skips the RawPayload when the wire is
byte-identical, so the newest event for a stable cohort can carry no bytes. Both reads join
``RawPayload`` so only payload-bearing events win, tie-broken on the (monotonic ULID) event id.
The scan is memoized — every build reads cohorts + events.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from usa_wa_adapter_legislature.span_emit import CitationTarget
from usa_wa_adapter_pdc.adapter import (
    HOUSE_WINNERS_RESOURCE_PREFIX,
    SENATE_WINNERS_RESOURCE_PREFIX,
)
from usa_wa_adapter_pdc.transport import parse_house_winners

logger = get_logger(__name__)


class PdcWinnerCohortProvider:
    """Archived PDC winner cohorts → the House span builder's + identifier links' inputs."""

    def __init__(self, *, session: AsyncSession, source_id: _ULID) -> None:
        self._session = session
        self._source_id = source_id
        self._cache: dict[str, dict[int, CitationTarget]] = {}

    async def _load_latest(self, prefix: str) -> dict[int, CitationTarget]:
        """Latest **payload-bearing** OK FetchEvent per election year under ``prefix``."""
        rows = (
            await self._session.execute(
                select(FetchEvent.resource_id, FetchEvent.id, FetchEvent.fetched_at)
                .join(RawPayload, RawPayload.fetch_event_id == FetchEvent.id)
                .where(
                    FetchEvent.source_id == self._source_id,
                    FetchEvent.resource_id.like(f"{prefix}%"),
                    FetchEvent.status == FetchStatus.ok,
                )
                .order_by(FetchEvent.fetched_at.desc(), FetchEvent.id.desc())  # newest first
            )
        ).all()
        latest: dict[int, CitationTarget] = {}
        for resource_id, event_id, fetched_at in rows:
            year = int(resource_id[len(prefix) :])
            latest.setdefault(year, (event_id, fetched_at, resource_id))
        return latest

    async def _events(self, prefix: str) -> dict[int, CitationTarget]:
        if prefix not in self._cache:
            self._cache[prefix] = await self._load_latest(prefix)
        return self._cache[prefix]

    async def house_events(self) -> dict[int, CitationTarget]:
        """``{election_year: citation target}`` for the archived House winner cohorts."""
        return dict(await self._events(HOUSE_WINNERS_RESOURCE_PREFIX))

    async def senate_events(self) -> dict[int, CitationTarget]:
        """``{election_year: citation target}`` for the archived Senate winner cohorts."""
        return dict(await self._events(SENATE_WINNERS_RESOURCE_PREFIX))

    async def archived_house_years(self) -> list[int]:
        """Election years with an archived House cohort, ascending."""
        return sorted(await self._events(HOUSE_WINNERS_RESOURCE_PREFIX))

    async def archived_senate_years(self) -> list[int]:
        """Election years with an archived Senate cohort, ascending."""
        return sorted(await self._events(SENATE_WINNERS_RESOURCE_PREFIX))

    async def house_cohorts(self) -> dict[int, list[dict[str, Any]]]:
        """``{election_year: [House winner rows]}`` re-parsed offline from the archive."""
        return await self._cohorts(HOUSE_WINNERS_RESOURCE_PREFIX)

    async def senate_cohorts(self) -> dict[int, list[dict[str, Any]]]:
        """``{election_year: [Senate winner rows]}`` re-parsed offline from the archive."""
        return await self._cohorts(SENATE_WINNERS_RESOURCE_PREFIX)

    async def _cohorts(self, prefix: str) -> dict[int, list[dict[str, Any]]]:
        out: dict[int, list[dict[str, Any]]] = {}
        for year, (event_id, _fetched_at, resource_id) in (await self._events(prefix)).items():
            wire = (
                await self._session.execute(
                    select(RawPayload.body).where(RawPayload.fetch_event_id == event_id)
                )
            ).scalar_one_or_none()
            # Both cohorts are the same SODA row shape; parse_house_winners = parse_senate_winners.
            out[year] = parse_house_winners(wire) if wire else []
        logger.info("pdc_cohort_loaded", extra={"prefix": prefix, "years": len(out)})
        return out
