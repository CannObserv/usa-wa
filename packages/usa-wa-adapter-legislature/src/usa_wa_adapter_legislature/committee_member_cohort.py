"""Archive-first committee-member cohort provider (#82, Phase B).

Turns the archived historical member rosters into the span builder's two inputs:

- :meth:`archived_rosters` — ``{(biennium, committee_id): [member rows]}``, re-parsed
  **offline** from each ``committee-members-hist:<biennium>:<id>:…`` :class:`RawPayload`
  (written by the Phase A harvest) through the same ``GetCommitteeMembers`` binding the live
  pull uses, so a closed roster is never re-fetched.
- :meth:`fetch_event_map` — ``{(biennium, committee_id): (fetch_event_id, fetched_at,
  resource_id)}``, the per-roster provenance each membership span cites.

**"Latest" means latest event that actually stored bytes.** The runner re-records a
:class:`FetchEvent` on every forced re-pull (refreshing the TTL + the #55 content-hash
ledger) but skips the :class:`RawPayload` when the wire is byte-identical
(``AdapterRunner._archive_payload``). The daily member fan-out forces past the TTL, so from
its second run onward the newest event for a stable roster carries **no payload**. Ordering
on FetchEvent alone would therefore read the current biennium as an empty roster — silently
dropping it out of every membership span and closing the open-ended ones. Both reads join
:class:`RawPayload` so only payload-bearing events are candidates, tie-broken on the
(monotonic ULID) event id. Same guarantee ``sponsor_cohort._archived_wire`` gets from its
join; the scan is memoized because every build calls both accessors.

Unlike the sponsor provider there is **no live fallback**: a (biennium, committee) roster is
either archived or it isn't part of the span domain. The harvest owns pulling; this reads.
An empty-wire archive (the benign "no roster that biennium" Fault the transport swallows) is
a real ``RawPayload(body=b"")`` — it contributes no members but still anchors provenance.
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

#: ``(biennium, committee_source_id)`` — one archived roster per key.
RosterKey = tuple[str, str]


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
        self._events: dict[RosterKey, CitationTarget] | None = None

    async def _load_latest_events(self) -> dict[RosterKey, CitationTarget]:
        """Latest **payload-bearing** OK FetchEvent per (biennium, committee_id).

        The ``RawPayload`` join is load-bearing, not an optimization: a payload-less event is
        the runner's byte-identical-re-pull marker, and letting one win would blank the
        roster (see the module docstring). ``FetchEvent.id`` breaks a ``fetched_at`` tie."""
        rows = (
            await self._session.execute(
                select(FetchEvent.resource_id, FetchEvent.id, FetchEvent.fetched_at)
                .join(RawPayload, RawPayload.fetch_event_id == FetchEvent.id)
                .where(
                    FetchEvent.source_id == self._source_id,
                    FetchEvent.resource_id.like(f"{COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX}%"),
                    FetchEvent.status == FetchStatus.ok,
                )
                .order_by(FetchEvent.fetched_at.asc(), FetchEvent.id.asc())  # last write wins
            )
        ).all()
        latest: dict[RosterKey, CitationTarget] = {}
        for resource_id, event_id, fetched_at in rows:
            biennium, committee_id, _agency, _name = parse_committee_members_hist_resource_id(
                resource_id
            )
            latest[(biennium, committee_id)] = (event_id, fetched_at, resource_id)
        return latest

    async def _latest_events(self) -> dict[RosterKey, CitationTarget]:
        """Memoized :meth:`_load_latest_events` — every build reads it twice (rosters +
        citation targets), and the scan grows with the archive."""
        if self._events is None:
            self._events = await self._load_latest_events()
        return self._events

    async def fetch_event_map(self) -> dict[RosterKey, CitationTarget]:
        """``{(biennium, committee_id): (fetch_event_id, fetched_at, resource_id)}`` — the
        pull that delivered each roster's archived bytes."""
        return dict(await self._latest_events())

    async def archived_rosters(self) -> dict[RosterKey, list[dict[str, Any]]]:
        """``{(biennium, committee_id): [member rows]}`` re-parsed offline from the archive."""
        rosters: dict[RosterKey, list[dict[str, Any]]] = {}
        for key, (event_id, _fetched_at, resource_id) in (await self._latest_events()).items():
            wire = await self._payload_bytes(event_id)
            if not wire:
                # Empty archive = the swallowed "no roster that biennium" Fault (#82).
                logger.debug("committee_member_cohort_empty_wire", extra={"resource": resource_id})
                rosters[key] = []
                continue
            rosters[key] = await self._client.parse_historical_committee_members(wire)
        logger.info("committee_member_cohort_loaded", extra={"rosters": len(rosters)})
        return rosters

    async def _payload_bytes(self, event_id: _ULID) -> bytes | None:
        """The bytes archived under one event (present by construction — the event set is
        joined against ``RawPayload``); ``b""`` for a swallowed empty-roster Fault.

        Keyed on the **event id**, deliberately distinct from the sibling providers'
        ``_archived_wire(resource_id)`` — here the payload-bearing event was already resolved
        by :meth:`_load_latest_events`, so this only dereferences it."""
        return (
            await self._session.execute(
                select(RawPayload.body).where(RawPayload.fetch_event_id == event_id)
            )
        ).scalar_one_or_none()
