"""SyncEngine — the daemon brain (write path + read path).

Stateless over a descriptor registry + a :class:`PowerMapClient`. Every method
takes an explicit ``session`` and (where a clock matters) an explicit ``now`` so
the logic is deterministic and unit-testable. The long-running daemon (step 7)
owns the loops and the wall clock; this class owns the per-cycle work.

Write path (this module, step 3):
    sweep_unanchored → enqueue CREATE
    drain_outbox     → post observations, settle dispositions, back off on error,
                       dead-letter to UNAVAILABLE once the retry cap is exhausted
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.client import PowerMapClient, RetryableClientError
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.models import (
    OP_CREATE,
    OP_ENRICH,
    OP_UPDATE,
    STATUS_DELIVERED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    OutboxEntry,
    SyncState,
)
from clearinghouse_sync_powermap.retry import next_attempt_at

logger = get_logger(__name__)

#: SyncState stream key for the shared PM changes feed.
CHANGES_STREAM = "changes_feed"

#: Default transport-failure retry cap before an entry is dead-lettered to
#: ``UNAVAILABLE``. Because :func:`retry.backoff` ceilings at 1h after ~7
#: attempts, the first ~7 attempts burn ~2h of short backoffs and each later
#: attempt is hourly — so 60 attempts ≈ 2h + 53h ≈ 2.3 days of PM-outage
#: tolerance before an entry goes terminal. ``next_attempt_at`` deferrals
#: (dependencies-not-ready) do not increment ``attempts``, so they never count.
DEFAULT_MAX_ATTEMPTS = 60

#: Outcomes of applying one PM record under LWW (returned for observability/tests).
APPLY_INSERTED = "inserted"
APPLY_UPDATED = "updated"
APPLY_KEPT_LOCAL = "kept_local"
#: An update-only descriptor (org/person/role/assignment) declined to mirror a PM
#: record it has never produced (``upsert_from_pm`` returned None) — not an insert.
APPLY_SKIPPED = "skipped"

#: Transport-level failures that are genuinely transient and warrant a backoff
#: retry. Anything else (e.g. a bug in payload construction) propagates so it is
#: not silently masked as a retryable network blip.
TRANSIENT_EXCEPTIONS = (
    RetryableClientError,
    httpx.HTTPError,
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
)


@dataclass(frozen=True)
class OutboxBacklog:
    """Operator view of the outbox: terminal piles + overdue/aging PENDING work.

    ``pending`` counts all open entries; ``pending_due`` the subset already past
    ``next_attempt_at`` (i.e. should have been delivered by now). ``rejected`` and
    ``unavailable`` are the two terminal backlogs an operator must act on.
    ``oldest_pending_age_seconds`` is None when nothing is pending.
    """

    pending: int
    pending_due: int
    rejected: int
    unavailable: int
    oldest_pending_age_seconds: float | None


class SyncEngine:
    """Per-cycle sync work over a fixed descriptor registry."""

    def __init__(
        self,
        descriptors: Sequence[EntityDescriptor],
        client: PowerMapClient,
        *,
        batch_limit: int = 100,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self._by_type = {d.entity_type: d for d in descriptors}
        self._client = client
        self._batch_limit = batch_limit
        self._max_attempts = max_attempts

    def descriptor_for(self, entity_type: str) -> EntityDescriptor | None:
        return self._by_type.get(entity_type)

    # --- enqueue helpers ------------------------------------------------------

    async def _has_open_entry(self, session: AsyncSession, entity_type: str, local_id) -> bool:
        existing = (
            await session.execute(
                select(OutboxEntry.id).where(
                    OutboxEntry.entity_type == entity_type,
                    OutboxEntry.local_id == local_id,
                    OutboxEntry.status == STATUS_PENDING,
                )
            )
        ).first()
        return existing is not None

    async def _enqueue(
        self, session: AsyncSession, descriptor: EntityDescriptor, row, op: str
    ) -> OutboxEntry | None:
        """Insert an outbox entry unless one is already open for this row."""
        local_id = row.id
        if await self._has_open_entry(session, descriptor.entity_type, local_id):
            return None
        entry = OutboxEntry(entity_type=descriptor.entity_type, local_id=local_id, op=op)
        session.add(entry)
        await session.flush()
        return entry

    async def sweep_unanchored(self, session: AsyncSession, descriptor: EntityDescriptor) -> int:
        """Enqueue a CREATE for every locally-minted row with a null anchor.

        Keeps the adapter ignorant of the sidecar — it just writes rows; the
        sweep discovers the un-anchored ones and queues them.
        """
        anchor_col = getattr(descriptor.model, descriptor.anchor_column)
        rows = (
            (await session.execute(select(descriptor.model).where(anchor_col.is_(None))))
            .scalars()
            .all()
        )
        enqueued = 0
        for row in rows:
            # PM-first: try to find a pre-existing PM record before creating one,
            # so we never duplicate PM's curated tree (identifier-less backfill).
            pm_id = await descriptor.pm_match(self._client, session, row)
            if pm_id is not None:
                record = await descriptor.fetch_record(self._client, pm_id)
                if record is not None:
                    # Adopt PM's canonical fields + anchor; no create.
                    await descriptor.upsert_from_pm(session, record, existing=row)
                    self._adopt_remote_clock(descriptor, row, record)
                    # Enrich-on-match (#198): PM matched an identifier-less record by
                    # name — push our identifiers/names onto it so it gains the data
                    # we hold and future syncs match by identifier.
                    if descriptor.enrich_identifier_type and await descriptor.needs_enrich(
                        record, row
                    ):
                        await self._enqueue(session, descriptor, row, OP_ENRICH)
                else:
                    # Matched but detail fetch failed — still capture the anchor.
                    descriptor.set_anchor(row, pm_id)
                continue
            if await self._enqueue(session, descriptor, row, OP_CREATE):
                enqueued += 1
        return enqueued

    # --- outbox worker --------------------------------------------------------

    async def _due_entries(self, session: AsyncSession, now: datetime) -> Sequence[OutboxEntry]:
        # No row-level locking (``FOR UPDATE SKIP LOCKED``): correctness assumes a
        # single sidecar instance (process model B, one systemd unit). Two
        # concurrent daemons would double-send. If the deployment ever scales out,
        # add ``.with_for_update(skip_locked=True)`` here.
        return (
            (
                await session.execute(
                    select(OutboxEntry)
                    .where(
                        OutboxEntry.status == STATUS_PENDING,
                        OutboxEntry.next_attempt_at <= now,
                    )
                    .order_by(OutboxEntry.next_attempt_at)
                    .limit(self._batch_limit)
                )
            )
            .scalars()
            .all()
        )

    async def drain_outbox(self, session: AsyncSession, *, now: datetime) -> list[OutboxEntry]:
        """Process all due PENDING entries once. Returns the entries touched."""
        touched: list[OutboxEntry] = []
        for entry in await self._due_entries(session, now):
            descriptor = self.descriptor_for(entry.entity_type)
            if descriptor is None or not descriptor.write_enabled:
                # Dormant or unknown type — leave PENDING, do not spin on it.
                continue
            if await self._deliver(session, descriptor, entry, now):
                touched.append(entry)
        await session.flush()
        return touched

    async def _deliver(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        entry: OutboxEntry,
        now: datetime,
    ) -> bool:
        """Attempt one delivery. Returns False if the entry was dropped (so the
        caller omits it from the touched set), True otherwise."""
        row = await session.get(descriptor.model, entry.local_id)
        if row is None:
            # Source row vanished before delivery — the entry is moot, so drop it
            # rather than mark it DELIVERED (it never was).
            await session.delete(entry)
            return False

        if not await descriptor.dependencies_ready(session, row):
            # A PM prerequisite (parent org / person / role) is not anchored yet.
            # Defer without counting a failure: keep PENDING, re-check next cycle.
            entry.next_attempt_at = next_attempt_at(now, entry.attempts)
            entry.last_error = "dependencies not ready"
            logger.info(
                "powermap_observation_deferred",
                extra={"entity_type": entry.entity_type, "local_id": str(entry.local_id)},
            )
            return True

        if entry.op == OP_ENRICH:
            payload = await descriptor.to_enrich_observation(session, row)
        else:
            payload = await descriptor.to_observation(session, row)
        try:
            result = await self._client.post_observation(descriptor.observe_path, payload)
        except TRANSIENT_EXCEPTIONS as exc:  # back off and retry; bugs propagate
            self._fail_attempt(entry, now, repr(exc))
            return True

        entry.last_disposition = result.disposition
        if result.anchored:
            descriptor.set_anchor(row, result.pm_id)
            entry.status = STATUS_DELIVERED
            entry.last_error = None
        elif result.rejected:
            entry.status = STATUS_REJECTED
            entry.last_error = str(result.raw)
            logger.error(
                "powermap_observation_rejected",
                extra={
                    "entity_type": entry.entity_type,
                    "local_id": str(entry.local_id),
                    "raw": result.raw,
                },
            )
        else:
            # Unexpected disposition — count it as a failed attempt so an operator
            # can see it and it cannot loop forever.
            self._fail_attempt(entry, now, f"unexpected disposition: {result.disposition!r}")
        return True

    def _fail_attempt(self, entry: OutboxEntry, now: datetime, error: str) -> None:
        """Record one failed delivery attempt: increment ``attempts``, capture the
        error, and either reschedule (still PENDING) or dead-letter the entry to
        ``UNAVAILABLE`` once the transport-failure cap is reached.

        Shared by the transient-exception and unexpected-disposition paths so both
        honour the same cap. Deferrals (dependencies-not-ready) do not route here —
        they are not delivery failures and must not consume attempts.
        """
        entry.attempts += 1
        entry.last_error = error
        if entry.attempts >= self._max_attempts:
            entry.status = STATUS_UNAVAILABLE
            logger.error(
                "powermap_observation_unavailable",
                extra={
                    "entity_type": entry.entity_type,
                    "local_id": str(entry.local_id),
                    "attempts": entry.attempts,
                    "error": error,
                },
            )
            return
        entry.next_attempt_at = next_attempt_at(now, entry.attempts)
        logger.warning(
            "powermap_observation_retry",
            extra={
                "entity_type": entry.entity_type,
                "local_id": str(entry.local_id),
                "attempts": entry.attempts,
                "error": error,
            },
        )

    # --- operator surface -----------------------------------------------------

    async def backlog(self, session: AsyncSession, *, now: datetime) -> OutboxBacklog:
        """Summarise the outbox for an operator/alerting surface.

        Thin instance wrapper over :func:`outbox_backlog` (the read needs no
        descriptors or client, so the HTTP surface can call the free function
        without constructing an engine).
        """
        return await outbox_backlog(session, now=now)

    async def redrive_unavailable(self, session: AsyncSession, *, now: datetime) -> int:
        """Reset dead-lettered (``UNAVAILABLE``) entries back to ``PENDING``, due now.

        For operator use once PM has recovered: the same payloads are re-attempted
        on the next drain. ``REJECTED`` entries are intentionally left untouched —
        those are payload-level refusals, not transport outages, so a blind retry
        would just repeat the rejection. No user-friendly trigger yet (#16).
        """
        entries = (
            (
                await session.execute(
                    select(OutboxEntry).where(OutboxEntry.status == STATUS_UNAVAILABLE)
                )
            )
            .scalars()
            .all()
        )
        for entry in entries:
            entry.status = STATUS_PENDING
            entry.attempts = 0
            entry.next_attempt_at = now
        await session.flush()
        if entries:
            logger.info("powermap_outbox_redriven", extra={"count": len(entries)})
        return len(entries)

    # --- read path: LWW reconcile --------------------------------------------

    async def apply_record(
        self, session: AsyncSession, descriptor: EntityDescriptor, record: dict
    ) -> str:
        """Upsert one PM record into the local cache under last-write-wins.

        - No local row → insert (or, for update-only descriptors that decline to
          mirror an unproduced record, skip).
        - Local row strictly newer than the PM record → keep local; enqueue an
          UPDATE to push it up (only when the entity is write-enabled).
        - Otherwise (PM newer, or tie) → PM wins; overwrite the local row.
        """
        existing = await descriptor.local_match(session, record)
        if existing is None:
            row = await descriptor.upsert_from_pm(session, record)
            if row is None:
                # Update-only descriptor declined to mirror an unproduced record.
                return APPLY_SKIPPED
            self._adopt_remote_clock(descriptor, row, record)
            return APPLY_INSERTED

        lu_local = descriptor.last_updated(existing)
        lu_pm = descriptor.last_updated(record)
        if lu_local is not None and lu_pm is not None and lu_local > lu_pm:
            # Keep local field values, but still capture the PM anchor we just
            # learned — otherwise the row looks unsynced and the sweep re-queues it.
            if descriptor.anchor_value(existing) is None:
                pm_id = descriptor.pm_id_from_record(record)
                if pm_id is not None:
                    descriptor.set_anchor(existing, pm_id)
            if descriptor.write_enabled:
                await self._enqueue(session, descriptor, existing, OP_UPDATE)
            return APPLY_KEPT_LOCAL

        row = await descriptor.upsert_from_pm(session, record, existing=existing)
        self._adopt_remote_clock(descriptor, row, record)
        return APPLY_UPDATED

    def _adopt_remote_clock(
        self, descriptor: EntityDescriptor, row: object | None, record: dict
    ) -> None:
        """Mirror the PM record's clock onto the just-upserted row so the next
        reconcile sees LWW parity, not a local ``now()``.

        This is the engine-wide guarantee that replaces per-descriptor
        ``updated_at`` bookkeeping: a freshly-cached row must not read as
        locally-newer, or it enqueues a spurious write-back (the go-live 403
        loop). ``row`` is None when a descriptor skipped an unmappable record.
        """
        if row is None:
            return
        pm_ts = descriptor.last_updated(record)
        if pm_ts is not None:
            descriptor.set_last_updated(row, pm_ts)

    # --- read path: full reconcile (backstop / jurisdictions' primary) -------

    async def reconcile(
        self, session: AsyncSession, descriptor: EntityDescriptor, *, now: datetime | None = None
    ) -> int:
        """Page through the entity's PM list endpoint, applying every record.

        Full-list reconcile is the periodic backstop, not the primary read. It is
        skipped for descriptors that opt out (``reconcile_enabled=False``) — the
        cohort-only producers, for which a full-list enumeration is the wrong scope
        (see the ``ReadSource`` note + CannObserv/usa-wa#13).
        """
        if (
            descriptor.read_source == "none"
            or descriptor.read_path is None
            or not descriptor.reconcile_enabled
        ):
            return 0
        applied = 0
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else None
            page = await self._client.list_entities(descriptor.read_path, params)
            for record in page.records:
                await self.apply_record(session, descriptor, record)
                applied += 1
            cursor = page.cursor
            if not cursor:
                break
        if now is not None:
            state = await self._get_or_create_state(session, _reconcile_stream(descriptor))
            state.last_reconcile_at = now
        await session.flush()
        return applied

    # --- read path: changes feed (incremental primary for person/org) --------

    async def process_feed(self, session: AsyncSession, *, limit: int = 100) -> int:
        """Pull one batch of changes, apply them, and advance the cursor.

        The feed yields ``(entity_type, id, change_kind)`` only, so each change
        is resolved to a full record via :meth:`PowerMapClient.get_entity`
        before upsert. Deletes are skipped at MVP (archival is a later concern).
        """
        state = await self._get_or_create_state(session, CHANGES_STREAM)
        page = await self._client.get_changes(state.cursor, limit=limit)
        applied = 0
        for item in page.items:
            descriptor = self.descriptor_for(item.entity_type)
            if descriptor is None or descriptor.read_source == "none":
                continue
            if item.change_kind == "deleted":
                continue
            record = await descriptor.fetch_record(self._client, item.entity_id)
            if record is None:
                continue
            await self.apply_record(session, descriptor, record)
            applied += 1
        if page.cursor is not None:
            state.cursor = page.cursor
        await session.flush()
        return applied

    # --- sync-state helpers ---------------------------------------------------

    async def _get_or_create_state(self, session: AsyncSession, stream: str) -> SyncState:
        state = (
            await session.execute(select(SyncState).where(SyncState.stream == stream))
        ).scalar_one_or_none()
        if state is None:
            state = SyncState(stream=stream)
            session.add(state)
            await session.flush()
        return state


async def outbox_backlog(session: AsyncSession, *, now: datetime) -> OutboxBacklog:
    """Summarise the outbox by status for an operator/alerting surface.

    Counts entries by status and reports how overdue/old the open work is, so a
    perpetually-retrying or dead-lettered row is visible rather than buried. Free
    function (no descriptors/client needed) so the HTTP health surface can read
    the backlog without building a :class:`SyncEngine`.
    """
    by_status = (
        await session.execute(
            select(
                OutboxEntry.status,
                func.count(),
                func.min(OutboxEntry.created_at),
            ).group_by(OutboxEntry.status)
        )
    ).all()
    counts = {status: (n, oldest) for status, n, oldest in by_status}
    pending_n, oldest_pending = counts.get(STATUS_PENDING, (0, None))
    pending_due = (
        await session.execute(
            select(func.count()).where(
                OutboxEntry.status == STATUS_PENDING,
                OutboxEntry.next_attempt_at <= now,
            )
        )
    ).scalar_one()
    age = (now - oldest_pending).total_seconds() if oldest_pending is not None else None
    return OutboxBacklog(
        pending=pending_n,
        pending_due=pending_due,
        rejected=counts.get(STATUS_REJECTED, (0, None))[0],
        unavailable=counts.get(STATUS_UNAVAILABLE, (0, None))[0],
        oldest_pending_age_seconds=age,
    )


def _reconcile_stream(descriptor: EntityDescriptor) -> str:
    return f"reconcile:{descriptor.entity_type}"
