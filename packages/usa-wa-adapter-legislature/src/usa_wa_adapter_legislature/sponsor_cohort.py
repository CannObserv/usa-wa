"""Archive-first sponsor-roster cohort provider (#78 increment 2b, Phase B).

Turns a biennium into its list of ``GetSponsors`` member rows — the member analog of
:class:`~usa_wa_adapter_legislature.committee_roster_cohort.CommitteeRosterCohortProvider`.
The span engine reads every archived biennium through this provider, projects the rows to
tenure observations (:mod:`sponsor_observations`), and builds merged spans.

**Archive-first, read-only.** Given a ``session`` + provenance ``source_id`` it reads the
latest archived ``sponsors:<biennium>`` wire (:class:`RawPayload`, written by the #77
harvest / daily refresh) and re-parses it **offline** via
:meth:`~usa_wa_adapter_legislature.transport.WSLClient.parse_sponsors` — so a closed roster
is never re-pulled. Only an un-archived biennium falls back to a live ``GetSponsors`` pull
(left un-archived; archival belongs to the harvest). Constructed with no session it always
pulls live.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from usa_wa_adapter_legislature.adapter import SPONSORS_RESOURCE_PREFIX

logger = get_logger(__name__)


class _SponsorClient(Protocol):
    async def fetch_sponsors(self, biennium: str) -> Any: ...

    async def parse_sponsors(self, wire: bytes) -> list[dict[str, Any]]: ...


class SponsorRosterCohortProvider:
    """Biennium → ``[member rows]`` over a WSL client, archive-first."""

    def __init__(
        self,
        client: _SponsorClient,
        *,
        session: AsyncSession | None = None,
        source_id: _ULID | None = None,
    ) -> None:
        self._client = client
        self._session = session
        self._source_id = source_id

    async def cohort(self, biennium: str) -> list[dict[str, Any]]:
        """The biennium's raw member rows — re-parsed from the archive, else pulled live."""
        resource_id = f"{SPONSORS_RESOURCE_PREFIX}{biennium}"
        wire = await self._archived_wire(resource_id)
        if wire is not None:
            logger.info("sponsor_cohort_cache_hit", extra={"resource_id": resource_id})
            return await self._client.parse_sponsors(wire)
        logger.info("sponsor_cohort_live_pull", extra={"resource_id": resource_id})
        fetched = await self._client.fetch_sponsors(biennium)
        return fetched.records

    async def roster_map(self, bienniums: list[str]) -> dict[str, list[dict[str, Any]]]:
        """``{biennium: [member rows]}`` across ``bienniums`` — the span builder's input."""
        return {biennium: await self.cohort(biennium) for biennium in bienniums}

    async def fetch_event_map(self, bienniums: list[str]) -> dict[str, tuple[_ULID, datetime]]:
        """``{biennium: (fetch_event_id, fetched_at)}`` for each biennium's latest archived
        roster — the per-biennium provenance the span emission cites (#78, cite-every-biennium).
        Bienniums with no archived roster are omitted."""
        out: dict[str, tuple[_ULID, datetime]] = {}
        if self._session is None or self._source_id is None:
            return out
        for biennium in bienniums:
            resource_id = f"{SPONSORS_RESOURCE_PREFIX}{biennium}"
            row = (
                await self._session.execute(
                    select(FetchEvent.id, FetchEvent.fetched_at)
                    .where(
                        FetchEvent.source_id == self._source_id,
                        FetchEvent.resource_id == resource_id,
                        FetchEvent.status == FetchStatus.ok,
                    )
                    .order_by(FetchEvent.fetched_at.desc())
                    .limit(1)
                )
            ).first()
            if row is not None:
                out[biennium] = (row[0], row[1])
        return out

    async def archived_bienniums(self) -> list[str]:
        """Every biennium with an archived roster, ascending — the span domain."""
        if self._session is None or self._source_id is None:
            return []
        prefix = SPONSORS_RESOURCE_PREFIX
        rows = (
            await self._session.execute(
                select(FetchEvent.resource_id)
                .where(
                    FetchEvent.source_id == self._source_id,
                    FetchEvent.resource_id.like(f"{prefix}%"),
                    FetchEvent.status == FetchStatus.ok,
                )
                .distinct()
            )
        ).all()
        return sorted(rid[len(prefix) :] for (rid,) in rows)

    async def _archived_wire(self, resource_id: str) -> bytes | None:
        if self._session is None or self._source_id is None:
            return None
        stmt = (
            select(RawPayload.body)
            .join(FetchEvent, FetchEvent.id == RawPayload.fetch_event_id)
            .where(
                FetchEvent.source_id == self._source_id,
                FetchEvent.resource_id == resource_id,
                FetchEvent.status == FetchStatus.ok,
            )
            .order_by(FetchEvent.fetched_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
