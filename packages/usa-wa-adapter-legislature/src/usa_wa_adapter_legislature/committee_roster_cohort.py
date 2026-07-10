"""Archive-first committee-roster cohort provider (sub-project 3, Phase B).

Turns a biennium into the ``{source_id: LongName}`` cohort the full rename-chain
builder consumes — the standing-committee analog of
:class:`~usa_wa_adapter_legislature.meeting_cohort.MeetingCohortProvider`.

**Archive-first, read-only.** Given a ``session`` + provenance ``source_id`` it reads
the latest archived ``committees-roster:<biennium>`` wire (:class:`RawPayload`, written
by the Phase A harvest) and re-parses it **offline** via
:meth:`~usa_wa_adapter_legislature.transport.WSLClient.parse_committees` — so a closed
roster is never re-pulled. Only an un-archived biennium falls back to a live
``GetCommittees`` pull (left un-archived; archival belongs to the harvest). Constructed
with no session it always pulls live (e.g. an off-box dry preview).

The cohort name is WSL's raw ``LongName`` (per #46) — the same string the chain builder
diffs and emits, so PM sees exactly what WSL published (PM canonicalises on its side).
A blank ``LongName`` is dropped (can't seed a rename).
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from usa_wa_adapter_legislature.adapter import COMMITTEES_ROSTER_RESOURCE_PREFIX
from usa_wa_adapter_legislature.normalize.fields import clean_field

logger = get_logger(__name__)


def roster_cohort_names(records: list[dict[str, Any]]) -> dict[str, str]:
    """``{source_id: LongName}`` for a roster's committee records, dropping blank names."""
    cohort: dict[str, str] = {}
    for rec in records:
        source_id = rec.get("Id")
        long_name = clean_field(rec.get("LongName"))
        if source_id is None or long_name is None:
            continue
        cohort[str(source_id)] = long_name
    return cohort


class _CommitteeClient(Protocol):
    async def fetch_committees(self, biennium: str) -> Any: ...

    async def parse_committees(self, wire: bytes) -> list[dict[str, Any]]: ...


class CommitteeRosterCohortProvider:
    """Biennium → ``{source_id: LongName}`` over a WSL client, archive-first."""

    def __init__(
        self,
        client: _CommitteeClient,
        *,
        session: AsyncSession | None = None,
        source_id: _ULID | None = None,
    ) -> None:
        self._client = client
        self._session = session
        self._source_id = source_id

    async def cohort(self, biennium: str) -> dict[str, str]:
        resource_id = f"{COMMITTEES_ROSTER_RESOURCE_PREFIX}{biennium}"
        wire = await self._archived_wire(resource_id)
        if wire is not None:
            logger.info("roster_cohort_cache_hit", extra={"resource_id": resource_id})
            return roster_cohort_names(await self._client.parse_committees(wire))
        logger.info("roster_cohort_live_pull", extra={"resource_id": resource_id})
        fetched = await self._client.fetch_committees(biennium)
        return roster_cohort_names(fetched.records)

    async def roster_records(self, biennium: str) -> list[dict[str, Any]]:
        """The biennium's raw committee records (``Id``/``Name``/``Agency``/``LongName``),
        archive-first — the same wire :meth:`cohort` reads, undigested.

        The #82 member harvest fans ``GetCommitteeMembers(biennium, agency, Name)`` over these,
        so it needs the short ``Name`` + ``Agency`` the ``{source_id: LongName}`` cohort drops.

        The live fallback (an un-archived biennium) is **not** archived or hashed — it only
        enumerates. Run ``harvest_committees`` first to provenance the enumeration itself.
        """
        resource_id = f"{COMMITTEES_ROSTER_RESOURCE_PREFIX}{biennium}"
        wire = await self._archived_wire(resource_id)
        if wire is not None:
            logger.info("roster_records_cache_hit", extra={"resource_id": resource_id})
            return await self._client.parse_committees(wire)
        logger.info("roster_records_live_pull", extra={"resource_id": resource_id})
        fetched = await self._client.fetch_committees(biennium)
        return list(fetched.records)

    async def archived_bienniums(self) -> list[str]:
        """Every biennium with an archived roster, ascending — the chain's domain."""
        if self._session is None or self._source_id is None:
            return []
        prefix = COMMITTEES_ROSTER_RESOURCE_PREFIX
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
