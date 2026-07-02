"""AdapterRunner — generic orchestration around any :class:`BaseAdapter`.

Owns the cache-or-fetch decision, idempotent upsert, provenance writing,
and discovery iteration. Same code for every adapter.
"""

import hashlib
from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import inspect, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.adapter import (
    BaseAdapter,
    FetchedPayload,
    NormalizedBatch,
    ResourceRef,
)
from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.models import Base
from clearinghouse_core.provenance import (
    Citation,
    FetchEvent,
    FetchStatus,
    RawPayload,
    Source,
)

NATURAL_KEY: tuple[str, ...] = ("jurisdiction_id", "source", "source_id")
"""Default natural-key columns used for ON CONFLICT upserts.

This was the convention before the 2026-06-09 decoupling, when every canonical
table FK'd ``jurisdiction_id``. Post-decoupling, several tables (Organization,
Person, LegislativeSession, …) carry UNIQUE on just ``(source, source_id)`` so
they can hold rows with NULL jurisdiction. Adapters whose entity tables match
the shorter shape pass ``natural_key=("source", "source_id")`` to
:class:`AdapterRunner`."""


@dataclass(frozen=True)
class RunSummary:
    """Result of an ``AdapterRunner.refresh()`` call."""

    discovered: int = 0
    fetched: int = 0
    skipped_cache_hit: int = 0
    upserted_entities: int = 0
    errors: int = 0


class AdapterRunner:
    """Drives one :class:`BaseAdapter` against its :class:`Source` config.

    Construct one instance per (adapter, session) pair. The session is held for
    the lifetime of one logical run (one ``fetch_and_normalize`` or one
    ``refresh``) — callers manage transactions.
    """

    def __init__(
        self,
        adapter: BaseAdapter,
        session: AsyncSession,
        *,
        source: Source,
        jurisdiction: Jurisdiction,
        natural_key: tuple[str, ...] = NATURAL_KEY,
        fill_only: bool = False,
    ) -> None:
        self.adapter = adapter
        self.session = session
        self.source = source
        self.jurisdiction = jurisdiction
        self.natural_key = natural_key
        #: Insert-new / never-update on natural-key conflict (``ON CONFLICT DO
        #: NOTHING``). For discovery/archival runs whose canonical fields are owned
        #: downstream (e.g. the daily WSL refresh: PM curates ``name``/``acronym`` and
        #: the read-mirror resolves them, so re-writing them here would clobber the
        #: curation and bump ``updated_at`` — winning LWW against PM, #65). The seed
        #: ingest takes the same "floor, not authority" stance (#39). Archival + the
        #: per-fetch Citation are unaffected — they don't depend on the conflict policy.
        self.fill_only = fill_only

    async def fetch_and_normalize(self, resource_id: str, *, force: bool = False) -> int:
        """Cache-or-fetch one resource, then upsert its normalized entities.

        Returns the number of canonical entities upserted (0 on cache hit when
        not ``force``).
        """
        if not force:
            cached = await self._find_fresh_fetch_event(resource_id)
            if cached is not None:
                return 0

        payload = await self.adapter.fetch_one(resource_id)
        event = await self._archive_payload(resource_id, payload)
        batch = await self.adapter.normalize(payload)
        return await self._persist_batch(event, batch)

    async def _archive_payload(
        self, resource_id: str, payload: FetchedPayload, *, status: FetchStatus = FetchStatus.ok
    ) -> FetchEvent:
        """Write ``FetchEvent`` (+ deduped ``RawPayload``) for a retrieved payload —
        provenance only, no normalize/upsert (#62).

        The archive-only seam: keeps the #54 ``content_hash`` derivation on the single
        chokepoint (``_record_fetch_event``) so no call site hand-rolls hashing, without
        asserting any canonical entity. ``fetch_and_normalize`` delegates here for its
        archival phase; the dedup guard (``_payload_already_archived``) bounds RawPayload
        growth for byte-identical re-fetches exactly as before.

        ``status`` defaults to ``ok`` but is a parameter so a future caller can archive the
        wire of a fetch whose *normalization* failed (record the evidence, assert nothing).

        Private until a real second caller lands: no production path archives without
        normalizing yet, so the seam stays internal (it exists to let a future
        read-mostly-live consumer archive durably without reusing the upsert path — promote
        to public when that caller is written and its shape is known). See #62.
        """
        event = await self._record_fetch_event(resource_id, payload, status=status)
        if not await self._payload_already_archived(resource_id, event):
            await self._record_raw_payload(event, payload)
        return event

    async def refresh(self, since: datetime | None = None) -> RunSummary:
        """Iterate ``adapter.discover(since)`` and process each ref."""
        discovered = fetched = skipped = upserted = errors = 0
        refs: AsyncIterable[ResourceRef] = self.adapter.discover(since)
        async for ref in refs:
            discovered += 1
            try:
                upserted_count = await self.fetch_and_normalize(ref.resource_id)
                if upserted_count == 0:
                    skipped += 1
                else:
                    fetched += 1
                    upserted += upserted_count
            except Exception:  # noqa: BLE001  (runner is the single retry boundary)
                errors += 1
        return RunSummary(
            discovered=discovered,
            fetched=fetched,
            skipped_cache_hit=skipped,
            upserted_entities=upserted,
            errors=errors,
        )

    async def _find_fresh_fetch_event(self, resource_id: str) -> FetchEvent | None:
        """Return the most recent fetch event for this resource if still within TTL."""
        ttl_cutoff = datetime.now(UTC) - timedelta(days=self.source.cache_ttl_days)
        stmt = (
            select(FetchEvent)
            .where(
                FetchEvent.source_id == self.source.id,
                FetchEvent.resource_id == resource_id,
                FetchEvent.status == FetchStatus.ok,
                FetchEvent.fetched_at >= ttl_cutoff,
            )
            .order_by(FetchEvent.fetched_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _record_fetch_event(
        self, resource_id: str, payload: FetchedPayload, *, status: FetchStatus
    ) -> FetchEvent:
        # Integrity baseline (#54): every fetch event carries a content hash, never
        # NULL. The runner is the single chokepoint so no adapter can forget it. An
        # adapter-supplied hash wins (e.g. a streamed digest, or a hash over a wire
        # form distinct from the stored body); otherwise derive sha256(body). The
        # canonical hashed form is exactly RawPayload.body — see provenance.py.
        content_hash = payload.content_hash
        if content_hash is None:
            content_hash = hashlib.sha256(payload.body).digest()
        event = FetchEvent(
            source_id=self.source.id,
            resource_id=resource_id,
            resource_version_key=payload.resource_version_key,
            url=payload.url,
            fetched_at=payload.fetched_at,
            http_status=payload.http_status,
            content_hash=content_hash,
            etag=payload.etag,
            last_modified=payload.last_modified,
            status=status,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def _payload_already_archived(self, resource_id: str, event: FetchEvent) -> bool:
        """True when this resource's ``content_hash`` already has a stored RawPayload.

        An archival window (e.g. #39's daily current-window pull) re-fetches a *stable*
        resource id every run; when the wire is byte-identical the bytes are already
        archived under an earlier FetchEvent. We still record the new FetchEvent
        (refreshing the cache TTL and the content-hash ledger) but skip re-storing the
        identical payload, bounding RawPayload growth for unchanged windows. Scoped to
        ``(source, resource_id, content_hash)`` and excludes the just-written event, so
        an identical hash under a *different* resource keeps its own archived copy.

        One lookup per fetch, backed by the composite
        ``ix_clearinghouse_core_fetch_events_dedup`` index on
        ``(source_id, resource_id, content_hash)`` covering this exact predicate (#59)."""
        stmt = (
            select(RawPayload.id)
            .join(FetchEvent, FetchEvent.id == RawPayload.fetch_event_id)
            .where(
                FetchEvent.source_id == self.source.id,
                FetchEvent.resource_id == resource_id,
                FetchEvent.content_hash == event.content_hash,
                FetchEvent.id != event.id,
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def _record_raw_payload(self, event: FetchEvent, payload: FetchedPayload) -> RawPayload:
        raw = RawPayload(
            fetch_event_id=event.id,
            content_type=payload.content_type,
            body=payload.body,
            size_bytes=len(payload.body),
        )
        self.session.add(raw)
        await self.session.flush()
        return raw

    async def _persist_batch(self, event: FetchEvent, batch: NormalizedBatch) -> int:
        """Upsert each entity by natural key; write a default citation per entity.

        ``_upsert`` populates ``entity.id`` with the persisted row's ULID
        (whether INSERT or UPDATE), so Citation rows can reference it directly.
        """
        upserted = 0
        for entity in batch.entities:
            await self._upsert(entity)
            citation = Citation(
                entity_type=_citation_type(entity),
                entity_id=entity.id,
                fetch_event_id=event.id,
                field_path=None,
                confidence=self.source.reliability,
                asserted_at=event.fetched_at,
            )
            self.session.add(citation)
            upserted += 1
        for fc in batch.citations:
            self.session.add(
                Citation(
                    entity_type=_citation_type(fc.entity),
                    entity_id=fc.entity.id,
                    fetch_event_id=event.id,
                    field_path=fc.field_path,
                    confidence=fc.confidence,
                    asserted_at=event.fetched_at,
                )
            )
        await self.session.flush()
        return upserted

    async def _upsert(self, entity: Base) -> None:
        """ON CONFLICT DO UPDATE on ``self.natural_key``; populate ``entity.id``.

        After the statement runs we ``SELECT id`` back by the natural key so
        ``entity.id`` always points at the row actually persisted — whether
        this was an INSERT (fresh ULID) or an UPDATE (existing row's ULID).
        Without that, downstream Citation rows pick up the in-memory ULID we
        generated locally and dangle whenever the conflict path triggered.
        """
        mapper = inspect(entity).mapper
        table = mapper.local_table
        cols = {c.key: getattr(entity, c.key) for c in mapper.columns if hasattr(entity, c.key)}
        # Only include columns actually set on the entity (skip defaults that
        # SQLAlchemy will provide via server_default).
        cols = {k: v for k, v in cols.items() if v is not None or k in self.natural_key}
        stmt = insert(table).values(**cols)
        update_cols = {k: v for k, v in cols.items() if k not in self.natural_key and k != "id"}
        if update_cols and not self.fill_only:
            stmt = stmt.on_conflict_do_update(
                index_elements=list(self.natural_key),
                set_=update_cols,
            )
        else:
            # fill_only (#65) forces DO NOTHING even when there are updatable columns:
            # an existing row is left untouched so a downstream-owned field can't be
            # clobbered and ``updated_at`` isn't bumped. The id read-back below still
            # resolves the existing row's id for the Citation.
            stmt = stmt.on_conflict_do_nothing(index_elements=list(self.natural_key))
        await self.session.execute(stmt)

        # Read back the persisted id by natural key so the entity instance
        # tracks the row that's actually in the table (handles both INSERT
        # and UPDATE paths uniformly).
        nk_filter = [table.c[k] == cols[k] for k in self.natural_key if k in cols]
        lookup = select(table.c.id).where(*nk_filter)
        persisted_id = (await self.session.execute(lookup)).scalar_one_or_none()
        if persisted_id is not None:
            entity.id = persisted_id


def _citation_type(entity: Base) -> str:
    return entity.__class__.__name__.lower()
