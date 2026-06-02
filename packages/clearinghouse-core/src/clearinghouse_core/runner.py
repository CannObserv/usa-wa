"""AdapterRunner — generic orchestration around any :class:`BaseAdapter`.

Owns the cache-or-fetch decision, idempotent upsert, provenance writing,
and discovery iteration. Same code for every adapter.
"""

from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

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
"""Convention: every canonical entity carries these three columns; the
combined UNIQUE constraint drives idempotent upsert."""


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
    ) -> None:
        self.adapter = adapter
        self.session = session
        self.source = source
        self.jurisdiction = jurisdiction

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
        event = await self._record_fetch_event(resource_id, payload, status=FetchStatus.ok)
        await self._record_raw_payload(event, payload)
        batch = await self.adapter.normalize(payload)
        return await self._persist_batch(event, batch)

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
        event = FetchEvent(
            source_id=self.source.id,
            resource_id=resource_id,
            resource_version_key=payload.resource_version_key,
            url=payload.url,
            fetched_at=payload.fetched_at,
            http_status=payload.http_status,
            content_hash=payload.content_hash,
            etag=payload.etag,
            last_modified=payload.last_modified,
            status=status,
        )
        self.session.add(event)
        await self.session.flush()
        return event

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
        """Upsert each entity by natural key; write a default citation per entity."""
        upserted = 0
        for entity in batch.entities:
            await self._upsert(entity)
            citation = Citation(
                entity_type=_citation_type(entity),
                entity_id=_extract_id_after_upsert(entity, self.session),
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
                    entity_id=_extract_id_after_upsert(fc.entity, self.session),
                    fetch_event_id=event.id,
                    field_path=fc.field_path,
                    confidence=fc.confidence,
                    asserted_at=event.fetched_at,
                )
            )
        await self.session.flush()
        return upserted

    async def _upsert(self, entity: Base) -> None:
        """ON CONFLICT DO UPDATE on ``(jurisdiction_id, source, source_id)``."""
        mapper = inspect(entity).mapper
        cols = {c.key: getattr(entity, c.key) for c in mapper.columns if hasattr(entity, c.key)}
        # Only include columns actually set on the entity (skip defaults that
        # SQLAlchemy will provide via server_default).
        cols = {k: v for k, v in cols.items() if v is not None or k in NATURAL_KEY}
        stmt = insert(mapper.local_table).values(**cols)
        update_cols = {k: v for k, v in cols.items() if k not in NATURAL_KEY and k != "id"}
        if update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=list(NATURAL_KEY),
                set_=update_cols,
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=list(NATURAL_KEY))
        await self.session.execute(stmt)


def _citation_type(entity: Base) -> str:
    return entity.__class__.__name__.lower()


def _extract_id_after_upsert(entity: Base, session: AsyncSession) -> Any:
    """Return the entity's ULID after upsert. If the row was inserted, the
    instance's ``id`` is set already; if updated, we re-query by natural key.

    For MVP we assume the adapter populated ``id`` before passing the entity
    in. The runner relies on the adapter generating fresh ULIDs for new rows
    and reusing the existing ULID for known rows (via a pre-fetch lookup).
    Refinement: have the runner do the lookup itself based on natural key,
    populating ``entity.id`` if a row already exists.
    """
    return entity.id
