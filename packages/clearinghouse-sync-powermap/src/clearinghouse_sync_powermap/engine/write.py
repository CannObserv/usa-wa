"""OutboxWriter — the local → PM write path (#181).

Extracted from ``SyncEngine`` unchanged. Owns the whole outbox lifecycle: the four
enqueue triggers, the drain that delivers them, the terminal states they settle into,
and the operator's redrive surface.

    context  ←  anchors  ←  **write**  ←  read  ←  __init__ (the SyncEngine façade)

Depends on :class:`~clearinghouse_sync_powermap.engine.anchors.AnchorManager` (every
delivery stamps an anchor and consults the one-row-per-anchor guard) and on
:class:`~clearinghouse_sync_powermap.engine.context.EngineContext` (the descriptor
registry, the client, the tunables, and the PM read pause-and-resume the sweep's
PM-first match needs). It knows nothing about the read path, which is why the sweep
adopts a matched PM record through ``descriptor.upsert_from_pm`` directly rather than
through the reconcile's ``apply_record`` — that asymmetry is pre-existing, not
introduced by the split, and it is what keeps this module free of a cycle.

**Why the sweep lives here and not in ``read``.** ``sweep_unanchored`` reads from PM (one
``pm_match`` per unanchored row), so a plausible reading puts it on the read side. It is
filed here because its *product* is an outbox entry: it is the CREATE trigger, it shares
the re-enqueue blocking-status guard with every other trigger, and its PM read is a
lookup in service of that enqueue decision — not a mirror of PM state into the cache.

Four enqueue triggers, one ledger (``OutboxEntry``), one drainer
(:meth:`OutboxWriter.drain_outbox`). At most one OPEN entry per row (partial-unique
index), so the triggers can never double-queue the same row:

  1. CREATE  — :meth:`OutboxWriter.sweep_unanchored` finds an un-anchored local row, the
               ``EntityDescriptor.pm_match`` cascade (identifier → name → hierarchy)
               finds NO PM match → mint a new PM entity.
  2. ENRICH  — the sweep matched an identifier-less PM record by *name* (enrich-on-match,
               power-map#198): adopt PM's anchor, then push our identifier + carry
               evidence onto it keyed by ``pm_id``.
  3. UPDATE  — the read path's ``Reconciler.apply_record`` finds the local row strictly
               newer than PM under LWW → push our value up. Keyed by our *real* identifier.
  4. ENRICH  — the anchored-cohort reconcile re-evaluates an already-anchored row
               (usa-wa#34): PM lost / never had our identifier (trigger gap), or our carry
               payload drifted from the last one we sent (detection gap, local
               fingerprint). Re-attach by ``pm_id``.

  UPDATE vs ENRICH — the only essential overlap: ENRICH's payload is a SUBSET of UPDATE's
  ``to_observation`` (carry evidence, minus PM-curated parent/affiliations). They differ in
  KEYING: UPDATE keys by our real identifier — unsafe when PM does not hold it yet (PM mints
  a duplicate); ENRICH keys by ``pm_id`` and is always safe. So when both would fire for one
  row, ENRICH supersedes the UPDATE (:meth:`OutboxWriter._upgrade_blocking_update_to_enrich`).

Drain detail (:meth:`OutboxWriter.drain_outbox`): post observations, settle dispositions,
back off on transient error, dead-letter to UNAVAILABLE once the retry cap is exhausted (or
immediately on a permanent auth/scope block), and park to REJECTED on a permanent payload
refusal — a poison entry parks itself rather than rolling back the whole cycle.
"""

import re
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, case, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.client import (
    DeliveryBlockedError,
    PayloadRejectedError,
)
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.engine.anchors import AnchorManager
from clearinghouse_sync_powermap.engine.context import (
    TRANSIENT_EXCEPTIONS,
    EngineContext,
    enrich_fingerprint,
)
from clearinghouse_sync_powermap.models import (
    OP_CREATE,
    OP_ENRICH,
    OP_UPDATE,
    STATUS_DELIVERED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    EnrichFingerprint,
    OutboxEntry,
)
from clearinghouse_sync_powermap.retry import next_attempt_at

logger = get_logger(__name__)

#: Statuses that block re-enqueue of the same source row. PENDING is the open
#: delivery; UNAVAILABLE is a dead-letter that must not be silently re-minted by
#: the sweep (else the cap never halts retries, UNAVAILABLE rows accumulate, and
#: a redrive would collide with the fresh PENDING on ``uq_powermap_outbox_open``).
#: REJECTED is intentionally excluded: it signals a data fix, after which the next
#: sweep should re-enqueue and re-attempt the corrected row.
#: PM's rejection reason when the observation names an ``identifier_type`` it has never
#: registered (usa-wa#257). A verdict on the TYPE, not the row: every sibling carrying it
#: will be refused identically, so the first one is a probe and the rest can wait.
_UNKNOWN_IDENTIFIER_TYPE = re.compile(r"unknown_identifier_type:\s*'([^']+)'")

_REENQUEUE_BLOCKING_STATUSES = (STATUS_PENDING, STATUS_UNAVAILABLE)


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


@dataclass
class DrainStats:
    """Per-drain observability tallies (usa-wa#108).

    88 orphaned PM assignments were minted in 24h with *no operator-visible number
    changing* — the disposition of each delivery and the fact of an in-place anchor
    overwrite were both invisible. The drain accumulates these here and the sidecar
    reads them off :attr:`SyncEngine.last_drain_stats` for the cycle summary. Reset at
    the start of every :meth:`OutboxWriter.drain_outbox` so it reflects one drain only.
    """

    #: Count of settled deliveries by PM disposition (``new`` / ``auto-attached`` /
    #: ``rejected``). Only deliveries that got a PM result are counted (a deferral has
    #: none). A rise in ``new`` for an anchored cohort is the orphan-mint signal.
    dispositions: Counter[str] = field(default_factory=Counter)
    #: Number of in-place anchor overwrites this drain (each = one orphaned PM id,
    #: recorded in :class:`~clearinghouse_sync_powermap.models.AnchorReanchor`).
    reanchors: int = 0
    #: Number of deliveries this drain that came back with a non-empty ``unapplied`` set
    #: (power-map#311b — PM matched but withheld an ``end_date``/``is_current`` delta). With
    #: anchored assignments delivered id-addressed this should stay 0; a rise means a delta
    #: PM refused on a natural-key path.
    unapplied: int = 0
    #: Number of stable ``auto-attached`` re-observes this drain that reached the
    #: non-convergence threshold (usa-wa#112) — a row PM keeps matching but not applying,
    #: re-sending an identical payload every reconcile cycle. The standing set is queried
    #: separately (:func:`~clearinghouse_sync_powermap.engine.anchors.nonconverging_count`)
    #: for the cycle summary + rise alert.
    non_converging: int = 0


class OutboxWriter:
    """The outbox: enqueue triggers, the drain, the terminal states, the redrive."""

    def __init__(self, ctx: EngineContext, anchors: AnchorManager) -> None:
        self._ctx = ctx
        self._anchors = anchors
        #: Outbox ids already surfaced as deferred-stuck this process, so the WARNING
        #: fires once per wedged entry rather than every cycle (#15 throttle). Bounded
        #: by the live stuck-entry count; a daemon restart re-warns once (acceptable).
        self._warned_stuck: set = set()
        #: (entity_type, local_id) pairs whose local-newer re-enqueue is currently
        #: suppressed as an identical-payload replay of a REJECTED UPDATE (#132). Same
        #: throttle shape as ``_warned_stuck``: WARNING once per row, INFO thereafter;
        #: cleared when the guard stands down (payload changed / entry moved on), so a
        #: second episode after a fix WARNs again. A restart re-warns once (acceptable).
        self._warned_reject_replay: set = set()
        #: Per-drain observability tallies (usa-wa#108), reset at each drain start and
        #: read by the sidecar's cycle summary. Defaults so a caller that reads it before
        #: any drain gets an empty, safe value.
        self._last_drain_stats = DrainStats()
        #: Identifier types PM has told us it does not know (usa-wa#257). Populated from a
        #: rejection reason, consulted before every delivery, and discarded the moment PM
        #: accepts one — so a registration self-heals with no operator step. Process-local
        #: by design: a restart re-probes with a single request, which is the correct cost
        #: of not knowing.
        self._blocked_identifier_types: set[str] = set()
        #: Blocked types already re-probed in the current drain. The block is **one probe
        #: per drain**, not a hard stop: a hard stop would latch forever, because the only
        #: thing that can clear it is a delivery it forbids. One request per cycle is the
        #: standing cost of a missing registration, against 2,494 without the breaker.
        self._probed_identifier_types: set[str] = set()

    @property
    def blocked_identifier_types(self) -> set[str]:
        """Identifier types PM refused as unknown; deliveries carrying one are deferred."""
        return self._blocked_identifier_types

    @property
    def last_drain_stats(self) -> DrainStats:
        """Disposition + re-anchor tallies from the most recent :meth:`drain_outbox`."""
        return self._last_drain_stats

    # --- enqueue helpers ------------------------------------------------------

    async def _has_blocking_entry(self, session: AsyncSession, entity_type: str, local_id) -> bool:
        existing = (
            await session.execute(
                select(OutboxEntry.id).where(
                    OutboxEntry.entity_type == entity_type,
                    OutboxEntry.local_id == local_id,
                    OutboxEntry.status.in_(_REENQUEUE_BLOCKING_STATUSES),
                )
            )
        ).first()
        return existing is not None

    async def enqueue(
        self, session: AsyncSession, descriptor: EntityDescriptor, row, op: str
    ) -> OutboxEntry | None:
        """Insert an outbox entry unless an open or dead-lettered one already exists
        for this row (see :data:`_REENQUEUE_BLOCKING_STATUSES`)."""
        local_id = row.id
        if await self._has_blocking_entry(session, descriptor.entity_type, local_id):
            return None
        entry = OutboxEntry(entity_type=descriptor.entity_type, local_id=local_id, op=op)
        session.add(entry)
        await session.flush()
        return entry

    async def rejected_identical_update(
        self, session: AsyncSession, descriptor: EntityDescriptor, row
    ) -> bool:
        """Whether re-enqueueing ``row`` would replay the exact UPDATE PM just refused (#132).

        The local-newer branch re-enqueues every reconcile while the local clock stays
        ahead of PM — and a rejected delivery adopts nothing, so a persistent 422
        (e.g. PM's ``chk_no_org_cycle``) would otherwise mint a fresh REJECTED entry
        every cycle forever: unbounded pile growth, and the #85 rise alert firing each
        cycle. This is the UPDATE analog of the #34 enrich-fingerprint stamp: true iff
        the row's latest outbox entry is a REJECTED UPDATE whose refused-payload hash
        (stamped by :meth:`_reject`) equals the row's *current* observation hash — only
        the provably-futile identical re-send is suppressed. Any payload change (the
        data fix) re-arms, keeping REJECTED fix-triggered-retry, never a dead end; a
        pre-#132 entry (NULL hash) can't prove identity and stands down.

        The standing skew stays operator-visible: WARNING once per row per process,
        INFO on later cycles (the #112 throttle shape, re-armed when the guard stands
        down so a second episode after a fix WARNs again).
        """
        latest = await session.scalar(
            select(OutboxEntry)
            .where(
                OutboxEntry.entity_type == descriptor.entity_type,
                OutboxEntry.local_id == row.id,
            )
            .order_by(OutboxEntry.id.desc())
            .limit(1)
        )
        key = (descriptor.entity_type, row.id)
        if (
            latest is None
            or latest.status != STATUS_REJECTED
            or latest.op != OP_UPDATE
            or latest.payload_hash is None
        ):
            self._warned_reject_replay.discard(key)
            return False
        if not await descriptor.dependencies_ready(session, row):
            # Identity can't be proven without building the observation, and the build
            # dereferences dependency anchors (the reason the noop-gate template hoists
            # this same guard, #102 CR). Stand down to the enqueue — its delivery
            # already defers on unready deps.
            self._warned_reject_replay.discard(key)
            return False
        payload = await descriptor.to_observation(session, row)
        if enrich_fingerprint(payload) != latest.payload_hash:
            self._warned_reject_replay.discard(key)
            return False
        first = key not in self._warned_reject_replay
        self._warned_reject_replay.add(key)
        log = logger.warning if first else logger.info
        log(
            "update_reject_replay_suppressed" if first else "update_reject_replay_still_suppressed",
            extra={
                "entity_type": descriptor.entity_type,
                "local_id": str(row.id),
                "source_id": getattr(row, "source_id", None),
                "last_error": latest.last_error,
            },
        )
        return True

    async def sweep_unanchored(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        *,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> int:
        """Enqueue a CREATE for every locally-minted row with a null anchor.

        Keeps the adapter ignorant of the sidecar — it just writes rows; the
        sweep discovers the un-anchored ones and queues them.

        Note: a REJECTED CREATE is re-enqueued here **unguarded** — the #132 replay
        guard covers only the local-newer UPDATE path. A rejected CREATE does carry
        its refused-payload hash (stamped by :meth:`_reject`), so if a persistent
        CREATE-422 cohort ever appears, a sweep-side identity check analogous to
        :meth:`rejected_identical_update` is a small follow-up.

        Batched (#7): rows are keyset-paged by primary key (``id > last_id``,
        ``sweep_batch_size`` at a time) rather than materialised all at once, so a
        first bulk identity ingest (persons/orgs in the thousands) never loads the
        whole unanchored backlog into memory in a single cycle. Keyset (not
        ``OFFSET``) is required because a CREATE leaves the anchor null until
        delivery — those rows stay in the ``anchor IS NULL`` set within the sweep,
        so advancing past the last processed id is what guarantees forward progress
        and termination instead of re-reading the same already-enqueued rows.

        When ``commit`` is supplied the sweep commits **per batch** (#92), mirroring
        the reconcile crawl: a first bulk ingest runs one PM match per row, so
        without an incremental boundary the whole sweep's enqueues + adoptions would
        ride one open transaction — and a later-batch failure would roll back every
        earlier batch's progress. Committing per page persists each batch; the keyset
        walk (``pk > last_id``) resumes past the committed rows even though a CREATE
        leaves the anchor NULL, so there is no re-processing.
        """
        anchor_col = getattr(descriptor.model, descriptor.anchor_column)
        pk_col = descriptor.model.id
        # Skip rows that already have an open/dead-lettered outbox entry (#93): their
        # CREATE is queued (or parked), so they stay ``anchor IS NULL`` until delivery —
        # re-running ``pm_match`` (a PM read per row) on them every cycle is pure waste,
        # and ``enqueue`` no-ops on the same guard anyway. Correlated NOT EXISTS on the
        # same ``(entity_type, local_id)`` in the re-enqueue-blocking statuses.
        already_queued = exists().where(
            OutboxEntry.entity_type == descriptor.entity_type,
            OutboxEntry.local_id == pk_col,
            OutboxEntry.status.in_(_REENQUEUE_BLOCKING_STATUSES),
        )
        enqueued = 0
        last_id = None
        while True:
            stmt = select(descriptor.model).where(anchor_col.is_(None), ~already_queued)
            if descriptor.deleted_column is not None:
                # Never re-create a terminally-deleted row — it would resurrect a
                # deliberately-deleted entity in PM (#31). An *archived* row keeps a
                # live anchor (deleted_at NULL) and so stays eligible (#42).
                stmt = stmt.where(descriptor.deleted_column_expr().is_(None))
            if last_id is not None:
                stmt = stmt.where(pk_col > last_id)
            stmt = stmt.order_by(pk_col).limit(self._ctx.sweep_batch_size)
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                break
            for row in rows:
                last_id = row.id
                if await self._sweep_row(session, descriptor, row):
                    enqueued += 1
            if commit is not None:
                # Bound the open transaction to one page of PM round-trips + persist
                # each batch's progress before the next (#92, mirroring the crawl).
                await session.flush()
                await commit()
            if len(rows) < self._ctx.sweep_batch_size:
                break
        return enqueued

    async def _sweep_row(self, session: AsyncSession, descriptor: EntityDescriptor, row) -> bool:
        """Process one unanchored row; return True iff a new CREATE was enqueued."""
        # PM-first: try to find a pre-existing PM record before creating one,
        # so we never duplicate PM's curated tree (identifier-less backfill). The match
        # is a PM read, so it pauses-and-resumes on a 429 (#92) — a first bulk ingest
        # runs one search per un-anchored row, exactly the burst that trips PM's limit;
        # a bare 429 here would abort the whole tick and lose the batch's progress.
        pm_id = await self._ctx.read_with_retry(
            lambda: descriptor.pm_match(self._ctx.client, session, row),
            log_extra={"read": "sweep_match", "entity_type": descriptor.entity_type},
        )
        # Adopting a pm_id another local row already anchors would violate the anchor
        # unique index (#86) and abort the whole tick — the sweep counterpart of the
        # drain guard. On a collision, decline the adopt and fall through to a CREATE
        # so the drain path owns the single park (UNAVAILABLE); ``log=False`` keeps
        # this per-cycle re-check quiet — the drain emits the authoritative line.
        # PM's observation dedup then arbitrates the CREATE: a true duplicate dedups
        # back to the taken id and parks UNAVAILABLE, while a false name-match (a
        # distinct entity) is minted as its own PM record — so a sweep collision does
        # not always dead-letter, and correctly so (PM owns identity).
        if pm_id is not None and not await self._anchors.anchor_taken(
            session, descriptor, row, pm_id, log=False
        ):
            record = await self._ctx.fetch_record_with_retry(descriptor, pm_id)
            if record is not None:
                # Adopt PM's canonical fields + anchor; no create.
                await descriptor.upsert_from_pm(session, record, existing=row)
                self._anchors.adopt_remote_clock(descriptor, row, record)
                # Enrich-on-match (#198): PM matched an identifier-less record by
                # name — push our identifiers/names onto it so it gains the data
                # we hold and future syncs match by identifier.
                await self.maybe_enqueue_enrich(session, descriptor, record, row)
            else:
                # Matched but detail fetch failed — still capture the anchor (clock
                # preserved: this row is not yet synced, so it must not read newer).
                self._anchors.stamp_anchor(descriptor, row, pm_id)
            return False
        return await self.enqueue(session, descriptor, row, OP_CREATE) is not None

    async def maybe_enqueue_enrich(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        record: dict,
        row,
        *,
        check_drift: bool = False,
    ) -> None:
        """Enqueue an ENRICH for an anchored row whose PM ``record`` lacks data it
        holds (enrich-on-match #198) or whose carry payload has drifted (#34).

        Two triggers:

        - **identifier missing** (:meth:`needs_enrich`, read from PM) — the original
          enrich-on-match: PM matched an identifier-less record by name. Always
          checked, so a held identifier that changes after anchoring (the #33
          legislature anchor-type switch) self-heals on the next reconcile.
        - **carry-payload drift** (``check_drift``, local fingerprint) — the current
          enrich payload differs from the last one we settled (:class:`EnrichFingerprint`).
          Catches a carry-field shape fix (#31) or a newly-added carry field reaching
          the existing cohort. Reconcile-only: the un-anchored sweep is a row's first
          match, so there is nothing to have drifted from.

        No-op unless the descriptor opts into enrichment (:attr:`enrich_identifier_type`).
        Idempotent: the :meth:`enqueue` blocking-status guard suppresses a duplicate
        while an entry for this row is open; the enqueued entry carries the payload
        hash so the settle path can stamp the fingerprint, after which an unchanged
        payload no longer drifts — no write-back loop. The fingerprint is local (what
        we last sent), so PM curating our evidence away never re-triggers.

        When the identifier is missing but an open ``OP_UPDATE`` already blocks the
        enqueue (the LWW ``KEPT_LOCAL`` path queued one), the UPDATE is upgraded to an
        ENRICH: an UPDATE is keyed by our *real* identifier, which PM cannot resolve
        when it lacks that identifier (duplicate risk), whereas ENRICH attaches by
        ``pm_id`` and carries the same evidence (carry fields ⊆ ``to_observation``,
        minus the PM-curated fields we must not re-assert). See finding #1, usa-wa#34.

        Drift-only with a blocking UPDATE is deliberately left as-is (no upgrade): the
        UPDATE resolves by an identifier PM *holds* (no duplicate risk) and carries a
        superset of the enrich evidence, so it already conveys the drifted carry —
        upgrading would only drop the non-carry local fields the UPDATE exists to push.
        """
        if not descriptor.enrich_identifier_type:
            return
        identifier_missing = await descriptor.needs_enrich(record, row)
        # Build the payload (and hash) only when it can matter — the sweep happy path
        # (identifier already present, no drift check) skips this entirely (#34 CR-4).
        if not identifier_missing and not check_drift:
            return
        payload = await descriptor.to_enrich_observation(session, row)
        fingerprint = enrich_fingerprint(payload)
        drift = check_drift and await self._enrich_payload_drifted(
            session, descriptor, row, fingerprint
        )
        if not (identifier_missing or drift):
            return
        entry = await self.enqueue(session, descriptor, row, OP_ENRICH)
        if entry is not None:
            entry.payload_hash = fingerprint
        elif identifier_missing:
            await self._upgrade_blocking_update_to_enrich(session, descriptor, row, fingerprint)

    async def maybe_enqueue_enrich_drift_only(
        self, session: AsyncSession, descriptor: EntityDescriptor, row
    ) -> None:
        """The **local** carry-payload drift half of :meth:`maybe_enqueue_enrich`, for the
        conditional-GET ``304`` path (usa-wa#160) where PM's ``record`` is unavailable.

        A ``304`` means PM's entity is unchanged since our last ``200``, so the
        ``identifier_missing`` trigger (read *from* PM) cannot have changed since — it was
        evaluated on that ``200``. But the carry-payload drift trigger is a **local→PM**
        signal: a newly-added carry field reaching the anchored cohort (#124 parent, #69
        identifiers, #31 contact) is a *local* change PM has never seen, so PM ``304``s
        every row and the full-fetch skip would otherwise strand the rollout — the drift
        enrich must still fire here or the field never propagates via the reconcile. Runs
        only the drift branch (no ``needs_enrich``, which needs the record); idempotent via
        the :meth:`enqueue` blocking-status guard, and quiet once each row's fingerprint
        is settled (identical to the pre-#160 unconditional reconcile's drift behaviour).
        """
        if not descriptor.enrich_identifier_type:
            return
        payload = await descriptor.to_enrich_observation(session, row)
        fingerprint = enrich_fingerprint(payload)
        if not await self._enrich_payload_drifted(session, descriptor, row, fingerprint):
            return
        entry = await self.enqueue(session, descriptor, row, OP_ENRICH)
        if entry is not None:
            entry.payload_hash = fingerprint

    async def _upgrade_blocking_update_to_enrich(
        self, session: AsyncSession, descriptor: EntityDescriptor, row, fingerprint: str
    ) -> None:
        """Convert a row's open ``OP_UPDATE`` to an ``OP_ENRICH`` in place (#34, finding #1).

        Called only when the identifier is missing and the enqueue was blocked. The
        blocking UPDATE is typically the one the LWW ``KEPT_LOCAL`` path queued this
        cycle, but it may also be an older un-drained UPDATE (a deps-not-ready deferral
        or a backed-off failed attempt) re-encountered on a later reconcile — either is
        safe to convert. An UPDATE keyed by an identifier PM does not hold risks minting
        a duplicate; ENRICH attaches by ``pm_id`` instead. Touches only a still-open
        ``PENDING`` UPDATE — a dead-lettered (``UNAVAILABLE``) entry or an already-``ENRICH``
        entry is left untouched. ``attempts``/``next_attempt_at`` are intentionally
        preserved: an inherited backoff only defers the (now-corrected) delivery by one
        cycle, not worth a reset.
        """
        entry = await session.scalar(
            select(OutboxEntry).where(
                OutboxEntry.entity_type == descriptor.entity_type,
                OutboxEntry.local_id == row.id,
                OutboxEntry.status == STATUS_PENDING,
                OutboxEntry.op == OP_UPDATE,
            )
        )
        if entry is not None:
            entry.op = OP_ENRICH
            entry.payload_hash = fingerprint

    async def _enrich_payload_drifted(
        self, session: AsyncSession, descriptor: EntityDescriptor, row, fingerprint: str
    ) -> bool:
        """Whether ``row``'s current enrich ``fingerprint`` differs from the last one
        we settled (#34). True when no stamp exists yet — so the pre-fingerprint
        anchored cohort re-enriches once (the automated successor to the manual
        backfill), then goes quiet once each row's stamp is written. Append-only and
        idempotent at PM, so the one-time cohort re-enrich is safe."""
        stored = await session.scalar(
            select(EnrichFingerprint.payload_hash).where(
                EnrichFingerprint.entity_type == descriptor.entity_type,
                EnrichFingerprint.local_id == row.id,
            )
        )
        return stored != fingerprint

    # --- outbox worker --------------------------------------------------------

    async def _due_entries(self, session: AsyncSession, now: datetime) -> Sequence[OutboxEntry]:
        # No row-level locking (``FOR UPDATE SKIP LOCKED``): correctness assumes a
        # single sidecar instance (process model B, one systemd unit). Two
        # concurrent daemons would double-send. If the deployment ever scales out,
        # add ``.with_for_update(skip_locked=True)`` here.
        #
        # Ordering is topological first, ``next_attempt_at`` second (usa-wa#96):
        # a dependency **root** (org/role) must be attempted before its dependents
        # (assignments) inside one ``LIMIT`` batch, or a flood of dependency-blocked
        # dependents whose ``next_attempt_at`` sorts earlier starves the root out of
        # the cut forever (attempts frozen). ``drain_priority`` maps each entity
        # type to its registry index; an unknown type (no descriptor) sorts last.
        # ``id`` is the final tiebreak so same-tier, same-``next_attempt_at`` entries
        # (a bulk produce clusters thousands) order stably cycle-to-cycle rather than
        # by nondeterministic physical order — the ULID id also roughly encodes
        # enqueue time, so the tiebreak is FIFO-ish within the tie.
        order_by: list[Any] = []
        priority = self._ctx.drain_priority
        if priority:  # case({}) is illegal; empty registry drains nothing anyway
            order_by.append(
                case(
                    priority,
                    value=OutboxEntry.entity_type,
                    else_=len(priority),
                )
            )
        order_by.append(OutboxEntry.next_attempt_at)
        order_by.append(OutboxEntry.id)
        return (
            (
                await session.execute(
                    select(OutboxEntry)
                    .where(
                        OutboxEntry.status == STATUS_PENDING,
                        OutboxEntry.next_attempt_at <= now,
                    )
                    .order_by(*order_by)
                    .limit(self._ctx.batch_limit)
                )
            )
            .scalars()
            .all()
        )

    async def drain_outbox(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        commit: Callable[[], Awaitable[None]] | None = None,
        chunk_size: int = 1,
    ) -> list[OutboxEntry]:
        """Process all due PENDING entries once. Returns the entries touched.

        Transaction boundary (#8): each :meth:`_deliver` makes a PM network round
        trip. When a ``commit`` callback is supplied, the drain commits every
        ``chunk_size`` delivered entries (and once more at the end for any
        remainder), so a slow PM never holds one open DB transaction across every
        round trip. ``chunk_size=1`` (the default with a hook) commits per entry —
        maximum durability, minimum lock hold; raise it to amortise commit cost
        when throughput matters. With no ``commit`` callback the legacy
        single-transaction behaviour is preserved (the caller owns the commit).
        """
        if chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        self._last_drain_stats = DrainStats()  # per-drain tallies (usa-wa#108)
        self._probed_identifier_types = set()  # one re-probe per blocked type per drain
        touched: list[OutboxEntry] = []
        since_commit = 0
        for entry in await self._due_entries(session, now):
            descriptor = self._ctx.descriptor_for(entry.entity_type)
            if descriptor is None or not descriptor.write_enabled:
                # Dormant or unknown type — leave PENDING, do not spin on it.
                continue
            if await self._deliver(session, descriptor, entry, now):
                touched.append(entry)
                since_commit += 1
                if commit is not None and since_commit >= chunk_size:
                    await session.flush()
                    await commit()
                    since_commit = 0
        await session.flush()
        if commit is not None and since_commit:
            await commit()
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

        if (
            entry.op == OP_CREATE
            and descriptor.is_deleted(row)
            and getattr(row, descriptor.anchor_column) is None
        ):
            # Retired after its CREATE was queued (usa-wa#259): a producer re-derivation
            # stopped asserting the row. Unanchored means PM never saw it, so there is
            # nothing to retract and delivering would mint exactly the record the producer
            # just decided should not exist. Same verdict as the vanished-row branch — drop
            # the entry, do not mark it DELIVERED. The sweep already refuses to *enqueue* a
            # deleted row; this closes the hole for one already in flight. An ANCHORED
            # tombstone is left alone: PM holds it, so retraction is the descriptors'
            # business (#31), not a silent drop.
            logger.info(
                "outbox_create_dropped_row_retired",
                extra={
                    "entity_type": descriptor.entity_type,
                    "local_id": str(entry.local_id),
                    "source_id": getattr(row, "source_id", None),
                },
            )
            await session.delete(entry)
            return False

        if not await descriptor.dependencies_ready(session, row):
            # A PM prerequisite (parent org / person / role) is not anchored yet.
            # Defer without counting a failure: keep PENDING, re-check next cycle.
            entry.next_attempt_at = next_attempt_at(now, entry.attempts)
            entry.last_error = "dependencies not ready"
            self._log_deferral(entry, now)
            return True

        if entry.op == OP_ENRICH:
            payload = await descriptor.to_enrich_observation(session, row)
        else:
            payload = await descriptor.to_observation(session, row)

        id_type = payload.get("identifier_type")
        if id_type in self._blocked_identifier_types:
            if id_type in self._probed_identifier_types:
                # Already re-probed this drain and PM still refuses the type (usa-wa#257).
                # The verdict is on the type, so this row cannot succeed either — defer
                # rather than spend a request to be told the same thing. Deferral keeps it
                # PENDING, so the cohort flushes by itself once the type is registered.
                entry.next_attempt_at = next_attempt_at(now, entry.attempts)
                entry.last_error = f"identifier_type not registered in PM: {id_type}"
                self._log_deferral(entry, now)
                return True
            # First row of a blocked type this drain: let it through as the re-probe. A
            # hard block would latch forever — the only thing that can clear it is a
            # delivery it forbids — so the breaker costs one request per cycle, not none.
            self._probed_identifier_types.add(id_type)

        try:
            result = await self._ctx.client.post_observation(descriptor.observe_path, payload)
        except TRANSIENT_EXCEPTIONS as exc:  # back off and retry; bugs propagate
            self._fail_attempt(entry, now, repr(exc))
            return True
        except DeliveryBlockedError as exc:
            # Permanent auth/scope rejection (e.g. 403): no retry clears it and the
            # cycle must not roll back, so dead-letter the entry now and continue.
            self._park_blocked(entry, repr(exc))
            return True
        except PayloadRejectedError as exc:
            # PM refused the payload (e.g. 422): park to the re-sweepable REJECTED
            # terminal state, like a `rejected` disposition, instead of crash-looping.
            # str(exc) (not repr) keeps last_error parallel to the disposition path's
            # str(result.raw) — a plain message, no `ClassName(...)` wrapper.
            # No raw= here: a 422 carries its detail in str(exc); PM's structured
            # `reason` (power-map#225) is a rejected-*disposition* concept, so the
            # log's `reason` field is correctly None on this validation-error path.
            await self._reject(session, entry, str(exc), payload=payload)
            return True

        entry.last_disposition = result.disposition
        self._last_drain_stats.dispositions[result.disposition] += 1
        if result.unapplied:
            # PM matched the observation but withheld a delta (power-map#311b): it applies
            # the one safe mutation (closing an open tenure) and echoes the rest here. With
            # anchored rows delivered id-addressed this should not happen; when it does, an
            # operator needs to see which field PM refused (a merged-state conflict, or an
            # escalation the producer isn't making).
            self._last_drain_stats.unapplied += 1
            logger.warning(
                "observation_deltas_unapplied",
                extra={
                    "entity_type": descriptor.entity_type,
                    "local_id": str(row.id),
                    "source_id": getattr(row, "source_id", None),
                    "disposition": result.disposition,
                    "unapplied": list(result.unapplied),
                },
            )
        if result.anchored:
            if await self._anchors.anchor_taken(session, descriptor, row, result.pm_id):
                # A *different* local row already holds this PM anchor — the
                # one-row-per-anchor invariant, DB-enforced (usa-wa#86). PM dedups
                # observations on (person, role, start_date), so two local rows can
                # resolve to one assignment id. Dead-letter to UNAVAILABLE (a
                # permanent block: the fix is an operator dedup, then a redrive — not
                # a data edit the sweep can auto-retry) rather than stamp a duplicate
                # and let the flush abort the whole tick. UNAVAILABLE is a *blocking*
                # status, so the row is not re-swept/re-POSTed and REJECTED entries
                # don't pile up cycle-over-cycle (which would trip the #85
                # rejection-rise email every cycle). The pre-check keeps the
                # transaction clean; the unique index is the hard backstop for any
                # writer the single-drainer check can't see.
                self._park_blocked(
                    entry,
                    f"anchor conflict: {descriptor.anchor_column}={result.pm_id}",
                )
                return True
            old_anchor = descriptor.anchor_value(row)
            if await self._anchors.record_reanchor(session, descriptor, row, result):
                self._last_drain_stats.reanchors += 1
            self._anchors.stamp_anchor(descriptor, row, result.pm_id)
            entry.status = STATUS_DELIVERED
            entry.last_error = None
            # PM accepted this type, so any standing block on it is stale (usa-wa#257) —
            # the registration self-heals the cohort with no operator step.
            if delivered_type := payload.get("identifier_type"):
                if delivered_type in self._blocked_identifier_types:
                    self._blocked_identifier_types.discard(delivered_type)
                    logger.info(
                        "powermap_identifier_type_unblocked",
                        extra={"identifier_type": delivered_type},
                    )
            await self._stamp_enrich_fingerprint(session, entry)
            if await self._anchors.track_convergence(
                session, descriptor, row, entry, result, old_anchor, payload
            ):
                self._last_drain_stats.non_converging += 1
        elif result.rejected:
            await self._reject(session, entry, str(result.raw), raw=result.raw, payload=payload)
        else:
            # Unexpected disposition — count it as a failed attempt so an operator
            # can see it and it cannot loop forever.
            self._fail_attempt(entry, now, f"unexpected disposition: {result.disposition!r}")
        return True

    async def _reject(
        self,
        session: AsyncSession,
        entry: OutboxEntry,
        error: str,
        *,
        raw: dict | None = None,
        payload: dict | None = None,
    ) -> None:
        """Park an entry to the ``REJECTED`` terminal state (PM refused the payload).

        Shared by the ``rejected`` disposition path and the permanent payload-error
        path. ``REJECTED`` is re-sweepable: once the data is fixed, the next sweep
        re-enqueues the corrected row.

        For an ENRICH it also stamps the fingerprint (#34): PM gave a terminal verdict
        on this exact payload, so the reconcile must not re-post the identical payload
        every cycle. A subsequent data/code fix changes the payload hash, which re-arms
        the drift trigger — so a rejection self-heals on the fix, not by blind retry.

        For CREATE/UPDATE the delivered ``payload`` is hashed onto the entry instead
        (#132) — the refused-payload record :meth:`rejected_identical_update` compares
        against, so the local-newer re-enqueue can skip a provably-futile identical
        re-send. An ENRICH keeps its enqueue-time hash (the #34 contract copies it to
        :class:`EnrichFingerprint`); re-hashing at delivery time could silently diverge
        from what the fingerprint stamp records.
        """
        entry.status = STATUS_REJECTED
        entry.last_error = error
        reason = raw.get("reason") if isinstance(raw, dict) else None
        if isinstance(reason, str) and (match := _UNKNOWN_IDENTIFIER_TYPE.search(reason)):
            # Arm the breaker (usa-wa#257): every sibling carrying this type would be
            # refused identically, so the rest of the cohort defers instead of being spent.
            slug = match.group(1)
            # The rejection we just took IS this drain's probe of the type, so no further
            # row need re-probe it until the next drain.
            self._probed_identifier_types.add(slug)
            if slug not in self._blocked_identifier_types:
                self._blocked_identifier_types.add(slug)
                logger.error(
                    "powermap_identifier_type_unknown_blocking_cohort",
                    extra={"identifier_type": slug, "entity_type": entry.entity_type},
                )
        if payload is not None and entry.op != OP_ENRICH:
            entry.payload_hash = enrich_fingerprint(payload)
        await self._stamp_enrich_fingerprint(session, entry)
        logger.error(
            "powermap_observation_rejected",
            extra={
                "entity_type": entry.entity_type,
                "local_id": str(entry.local_id),
                # PM's diagnostic reason (power-map#225), promoted from raw to a
                # top-level field so a rejection is greppable without parsing raw.
                "reason": raw.get("reason") if isinstance(raw, dict) else None,
                "raw": raw,
            },
        )

    async def _stamp_enrich_fingerprint(self, session: AsyncSession, entry: OutboxEntry) -> None:
        """Record an ENRICH entry's settled payload hash as the row's fingerprint (#34).

        Idempotent upsert keyed on ``(entity_type, local_id)``. No-op unless the entry
        is an ENRICH carrying a ``payload_hash`` (CREATE/UPDATE never stamp). Called on
        a terminal PM verdict (delivered or rejected) — not on transient/blocked
        failures, which retry the same payload and must leave the prior stamp intact.
        After stamping, :meth:`_enrich_payload_drifted` returns False for an unchanged
        payload, so the reconcile stops re-enqueuing — convergence.
        """
        if entry.op != OP_ENRICH or entry.payload_hash is None:
            return
        existing = await session.scalar(
            select(EnrichFingerprint).where(
                EnrichFingerprint.entity_type == entry.entity_type,
                EnrichFingerprint.local_id == entry.local_id,
            )
        )
        if existing is None:
            session.add(
                EnrichFingerprint(
                    entity_type=entry.entity_type,
                    local_id=entry.local_id,
                    payload_hash=entry.payload_hash,
                )
            )
        else:
            existing.payload_hash = entry.payload_hash

    def _park_blocked(self, entry: OutboxEntry, error: str) -> None:
        """Immediately dead-letter a permanently-blocked entry to ``UNAVAILABLE``.

        Unlike :meth:`_fail_attempt`, this does not consume the retry budget — a
        permanent auth/scope rejection (e.g. 403) will never succeed on retry, so
        burning ``max_attempts`` cycles on it is pure waste. Recovery is
        operator-driven: fix the credential/scope, then :meth:`redrive_unavailable`.
        """
        entry.status = STATUS_UNAVAILABLE
        entry.last_error = error
        logger.error(
            "powermap_observation_unavailable",
            extra={
                "entity_type": entry.entity_type,
                "local_id": str(entry.local_id),
                "attempts": entry.attempts,
                "error": error,
                # Distinguishes a permanent auth/scope block from a transport-cap
                # dead-letter (see _fail_attempt) — both share this event name.
                "reason": "blocked",
            },
        )

    def _log_deferral(self, entry: OutboxEntry, now: datetime) -> None:
        """Log a deps-not-ready deferral, escalating to a WARNING once the entry has
        been deferred longer than the stuck threshold (#15).

        A deferral never increments ``attempts``, so the transport-failure cap can
        never dead-letter a permanently un-anchorable prerequisite — it would defer
        forever and invisibly. An aged, still-never-attempted entry is exactly that
        signature, so it is surfaced as a distinct, alertable WARNING rather than
        buried in the routine deferral INFO stream. Age reuses ``created_at`` (no
        schema migration); a row created in this very cycle has no stamp yet
        (``created_at is None`` pre-server-flush) — treat that as not-yet-stuck.

        Throttled (#15 CR): a wedged entry is re-checked every cycle, so the stuck
        WARNING fires only the first time each id is seen stuck this process (then
        falls back to the routine INFO) — one actionable signal, not per-cycle spam.
        """
        age = (now - entry.created_at) if entry.created_at is not None else None
        is_stuck = age is not None and age >= self._ctx.deferred_stuck_threshold
        extra = {"entity_type": entry.entity_type, "local_id": str(entry.local_id)}
        if is_stuck and entry.id not in self._warned_stuck:
            self._warned_stuck.add(entry.id)
            logger.warning(
                "powermap_observation_deferred_stuck",
                extra={**extra, "attempts": entry.attempts, "age_seconds": age.total_seconds()},
            )
        else:
            logger.info("powermap_observation_deferred", extra=extra)

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
        if entry.attempts >= self._ctx.max_attempts:
            entry.status = STATUS_UNAVAILABLE
            logger.error(
                "powermap_observation_unavailable",
                extra={
                    "entity_type": entry.entity_type,
                    "local_id": str(entry.local_id),
                    "attempts": entry.attempts,
                    "error": error,
                    # vs _park_blocked's "blocked": this is the transport/retry cap
                    # running out, not a permanent auth refusal.
                    "reason": "cap_exhausted",
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

    @staticmethod
    def _unavailable_scope(
        now: datetime, entity_type: str | None, older_than: timedelta | None
    ) -> list[ColumnElement[bool]]:
        """WHERE predicates selecting the re-drivable (``UNAVAILABLE``) rows in scope.

        Always pins ``status == UNAVAILABLE`` (the only re-drivable terminal pile —
        ``REJECTED`` is a payload refusal a blind retry would just repeat), then
        narrows by entity type and/or age (``created_at <= now - older_than``) when
        those filters are given. Shared by :meth:`count_unavailable` and
        :meth:`redrive_unavailable` so the scope can never drift between the
        preview count and the mutating flip.
        """
        filters = [OutboxEntry.status == STATUS_UNAVAILABLE]
        if entity_type is not None:
            filters.append(OutboxEntry.entity_type == entity_type)
        if older_than is not None:
            filters.append(OutboxEntry.created_at <= now - older_than)
        return filters

    async def count_unavailable(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        entity_type: str | None = None,
        older_than: timedelta | None = None,
    ) -> int:
        """Count re-drivable (``UNAVAILABLE``) entries matching the scope, non-mutating.

        Powers the ``dry_run`` preview and the operator-reported ``matched`` count
        without touching rows. ``limit`` is intentionally absent — this reports the
        full size of the in-scope dead-letter pile, not how many a capped flip
        would touch.
        """
        return (
            await session.execute(
                select(func.count())
                .select_from(OutboxEntry)
                .where(*self._unavailable_scope(now, entity_type, older_than))
            )
        ).scalar_one()

    async def redrive_unavailable(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        entity_type: str | None = None,
        older_than: timedelta | None = None,
        limit: int | None = None,
    ) -> int:
        """Reset dead-lettered (``UNAVAILABLE``) entries back to ``PENDING``, due now.

        For operator use once the cause is cleared — PM has recovered (transport
        cap exhausted) or the API key has been re-scoped (a permanent auth/scope
        block): attempts are zeroed, the stale ``last_error`` is cleared, and the
        same payloads are re-attempted on the next drain. ``REJECTED`` entries are
        intentionally left untouched — those are payload-level refusals, not
        transport/auth failures, so a blind retry would just repeat the rejection.

        Scope the flip with ``entity_type`` / ``older_than`` (against ``created_at``)
        and cap it with ``limit`` (oldest-first, so a bounded re-drive drains the
        longest-stuck work first). With no scope/limit it resets every UNAVAILABLE
        row, matching the original #5 recovery hook.

        Returns the number of rows actually flipped.

        Safe against ``uq_powermap_outbox_open`` because the enqueue guard
        (:data:`_REENQUEUE_BLOCKING_STATUSES`) keeps at most one PENDING/UNAVAILABLE
        entry per source row, so flipping never creates a second open row.
        """
        filters = self._unavailable_scope(now, entity_type, older_than)
        stmt = update(OutboxEntry)
        if limit is not None:
            # Postgres has no UPDATE ... LIMIT; select the oldest in-scope ids first.
            # Tiebreak on id so a capped flip is deterministic when rows share a
            # ``created_at`` (bulk inserts land on the same ``server_default now()``).
            scoped_ids = (
                select(OutboxEntry.id)
                .where(*filters)
                .order_by(OutboxEntry.created_at, OutboxEntry.id)
                .limit(limit)
            )
            stmt = stmt.where(OutboxEntry.id.in_(scoped_ids))
        else:
            stmt = stmt.where(*filters)
        result = await session.execute(
            stmt.values(
                status=STATUS_PENDING, attempts=0, next_attempt_at=now, last_error=None
            ).execution_options(synchronize_session=False)
        )
        count = result.rowcount
        if count:
            logger.info(
                "powermap_outbox_redriven",
                extra={"count": count, "entity_type": entity_type, "limit": limit},
            )
        return count


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


#: Cap on distinct-reason grouping in :func:`rejected_breakdown` — the REJECTED
#: pile is small by definition (each row needs a data fix); a pile past this cap
#: is itself the signal and the truncated breakdown still shows the shape.
_REJECTED_BREAKDOWN_LIMIT = 500
#: Reason strings are free text (a 422 detail can embed the whole payload); group
#: on a prefix so near-identical rejections collapse into one line.
_REASON_PREFIX_LEN = 120


async def rejected_breakdown(session: AsyncSession) -> dict[str, int]:
    """REJECTED entries grouped by (truncated) ``last_error`` reason (usa-wa#85).

    The per-entry ``powermap_observation_rejected`` log line fires once at park
    time and is never repeated — the #84 postmortem found 12 rejections that sat
    unnoticed for a week. This is the periodic re-surface: the sidecar logs it in
    the cycle summary and alerts on a count rise. Free function like
    :func:`outbox_backlog` so any operator surface can read it without an engine.
    """
    reasons = (
        await session.execute(
            select(OutboxEntry.last_error)
            .where(OutboxEntry.status == STATUS_REJECTED)
            .limit(_REJECTED_BREAKDOWN_LIMIT)
        )
    ).scalars()
    breakdown: dict[str, int] = {}
    for reason in reasons:
        key = (reason or "(no reason recorded)")[:_REASON_PREFIX_LEN]
        breakdown[key] = breakdown.get(key, 0) + 1
    return breakdown
