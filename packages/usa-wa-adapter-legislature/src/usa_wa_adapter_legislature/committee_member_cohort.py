"""Archive-first committee-member cohort provider (#82, Phase B).

Turns the archived historical member rosters into the span builder's two inputs:

- :meth:`archived_rosters` — ``{(biennium, committee_id): [member rows]}``, re-parsed
  **offline** from each ``committee-members-hist:<biennium>:<id>:…`` :class:`RawPayload`
  (written by the Phase A harvest) through the same ``GetCommitteeMembers`` binding the live
  pull uses, so a closed roster is never re-fetched.
- :meth:`fetch_event_map` — ``{(biennium, committee_id): (fetch_event_id, fetched_at,
  resource_id)}``, the per-roster provenance each membership span cites.

Unlike the sponsor provider there is **no live fallback**: a (biennium, committee) roster is
either archived or it isn't part of the span domain. The harvest owns pulling; this reads.
An empty-wire archive (the benign "no roster that biennium" Fault the transport swallows)
contributes no members but still anchors provenance.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from usa_wa_adapter_legislature.adapter import (
    COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX,
    parse_committee_members_hist_resource_id,
)
from usa_wa_adapter_legislature.span_emit import CitationTarget

logger = get_logger(__name__)


class _MemberClient(Protocol):
    async def parse_historical_committee_members(self, wire: bytes) -> list[dict[str, Any]]: ...


class CommitteeMemberCohortProvider:
    """Archived historical rosters → the committee-membership span builder's inputs."""

    def __init__(
        self,
        client: _MemberClient,
        *,
        session: AsyncSession,
        source_id: _ULID,
    ) -> None:
        self._client = client
        self._session = session
        self._source_id = source_id

    async def _latest_events(self) -> dict[tuple[str, str], tuple[_ULID, Any, str]]:
        """Latest OK FetchEvent per (biennium, committee_id) — one archived roster each."""
        rows = (
            await self._session.execute(
                select(FetchEvent.resource_id, FetchEvent.id, FetchEvent.fetched_at)
                .where(
                    FetchEvent.source_id == self._source_id,
                    FetchEvent.resource_id.like(f"{COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX}%"),
                    FetchEvent.status == FetchStatus.ok,
                )
                .order_by(FetchEvent.fetched_at.asc())  # later rows overwrite → latest wins
            )
        ).all()
        latest: dict[tuple[str, str], tuple[_ULID, Any, str]] = {}
        for resource_id, event_id, fetched_at in rows:
            biennium, committee_id, _agency, _name = parse_committee_members_hist_resource_id(
                resource_id
            )
            latest[(biennium, committee_id)] = (event_id, fetched_at, resource_id)
        return latest

    async def fetch_event_map(self) -> dict[tuple[str, str], CitationTarget]:
        """``{(biennium, committee_id): (fetch_event_id, fetched_at, resource_id)}``."""
        return dict(await self._latest_events())

    async def archived_rosters(self) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """``{(biennium, committee_id): [member rows]}`` re-parsed offline from the archive."""
        rosters: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for key, (event_id, _fetched_at, resource_id) in (await self._latest_events()).items():
            wire = (
                await self._session.execute(
                    select(RawPayload.body).where(RawPayload.fetch_event_id == event_id)
                )
            ).scalar_one_or_none()
            if not wire:
                # Empty archive = the swallowed "no roster that biennium" Fault (#82).
                logger.debug("committee_member_cohort_empty_wire", extra={"resource": resource_id})
                rosters[key] = []
                continue
            rosters[key] = await self._client.parse_historical_committee_members(wire)
        logger.info("committee_member_cohort_loaded", extra={"rosters": len(rosters)})
        return rosters
