"""SyncEngine — the daemon brain (write path + read path).

Stateless over a descriptor registry + a :class:`PowerMapClient`. Every method
takes an explicit ``session`` and (where a clock matters) an explicit ``now`` so
the logic is deterministic and unit-testable. The long-running daemon (step 7)
owns the loops and the wall clock; this class owns the per-cycle work.

Sync topology (the whole map, for readers + agents)
===================================================
Two directions, bidirectional sync between the local cache and PM. PM is the
system of record; the local cache is a query-latency mirror we *produce into*.

WRITE path (local → PM) — four triggers, one ledger (:class:`OutboxEntry`),
one drainer (:meth:`drain_outbox`). At most one OPEN entry per row (partial-
unique index), so the triggers can never double-queue the same row:

  1. CREATE  — :meth:`sweep_unanchored` finds an un-anchored local row, the
               :meth:`EntityDescriptor.pm_match` cascade (identifier → name →
               hierarchy) finds NO PM match → mint a new PM entity.
  2. ENRICH  — :meth:`sweep_unanchored` matched an identifier-less PM record by
               *name* (enrich-on-match, power-map#198): adopt PM's anchor, then
               push our identifier + carry evidence onto it keyed by ``pm_id``.
  3. UPDATE  — :meth:`apply_record` (read path, below) finds the local row is
               strictly newer than PM under LWW → push our value up. Keyed by
               our *real* identifier.
  4. ENRICH  — :meth:`_reconcile_anchored_cohort` re-evaluates an already-
               anchored row (usa-wa#34): PM lost / never had our identifier
               (trigger gap), or our carry payload drifted from the last one we
               sent (detection gap, local fingerprint). Re-attach by ``pm_id``.

  UPDATE vs ENRICH — the only essential overlap: ENRICH's payload is a SUBSET of
  UPDATE's ``to_observation`` (carry evidence, minus PM-curated parent/affiliations).
  They differ in KEYING: UPDATE keys by our real identifier — unsafe when PM does
  not hold it yet (PM mints a duplicate); ENRICH keys by ``pm_id`` and is always
  safe. So when both would fire for one row, ENRICH supersedes the UPDATE
  (:meth:`_upgrade_blocking_update_to_enrich`).

READ path (PM → local) — both converge on the single LWW arbiter
:meth:`apply_record` (PM-newer-or-tie → PM wins; local-newer → keep + maybe
enqueue an UPDATE), so the clock comparison lives in exactly one place:

  - :meth:`process_feed`            — incremental PRIMARY: PM's changes feed.
  - :meth:`reconcile` →
    :meth:`_reconcile_anchored_cohort` — bounded BACKSTOP: re-fetch only OUR
                                         anchored rows to recover a dropped feed
                                         event (and, since #34, to re-enrich).

  Enrich re-evaluation lives ONLY on the reconcile backstop, not the feed: the
  reconcile is the one path that already walks the whole anchored cohort, so a
  held-identifier change or carry drift self-heals on the reconcile cadence
  (hourly), not per feed event. Consolidating all three enrich triggers into
  ``apply_record`` is a tracked simplification (usa-wa#35).

Write-path drain detail (:meth:`drain_outbox`):
    post observations, settle dispositions, back off on transient error,
    dead-letter to UNAVAILABLE once the retry cap is exhausted (or immediately on
    a permanent auth/scope block), and park to REJECTED on a permanent payload
    refusal — a poison entry parks itself rather than rolling back the whole cycle.
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.client import (
    DeliveryBlockedError,
    PayloadRejectedError,
    PowerMapClient,
    RetryableClientError,
)
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid
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
    SyncState,
)
from clearinghouse_sync_powermap.retry import next_attempt_at

logger = get_logger(__name__)

#: SyncState stream key for the shared PM changes feed.
CHANGES_STREAM = "changes_feed"


def _utcnow() -> datetime:
    """Wall clock fallback for retire/heal timestamps when a caller supplies no
    ``now`` (the feed/reconcile paths thread one; this guards ad-hoc callers)."""
    return datetime.now(UTC)


def _canonicalize(obj: object) -> object:
    """Recursively normalise a payload for hashing: sort lists by content so order
    never affects the hash (dict keys are sorted by the dump step).

    Enrich carry fields are *evidence sets* (names, acronyms, contact methods) —
    their order carries no meaning, so two payloads holding the same evidence in a
    different order must hash equally. Sorting list items by their canonical JSON
    makes the hash robust to a descriptor that builds a carry list from a set/dict
    iteration (otherwise a nondeterministic order would re-enrich every cycle).
    """
    if isinstance(obj, dict):
        return {k: _canonicalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        items = [_canonicalize(v) for v in obj]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
    return obj


def enrich_fingerprint(payload: dict) -> str:
    """A stable content hash of an enrich observation payload (#34).

    Canonicalises the payload (sorted keys + sorted list items, compact separators,
    ``str`` fallback for ULIDs/datetimes) so the hash depends only on content — not
    key order, list order, or Python repr. Carry fields are evidence sets, so equal
    evidence hashes equally regardless of how a descriptor ordered it.
    """
    canonical = json.dumps(
        _canonicalize(payload), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: Default transport-failure retry cap before an entry is dead-lettered to
#: ``UNAVAILABLE``. Because :func:`retry.backoff` ceilings at 1h after ~7
#: attempts, the first ~7 attempts burn ~2h of short backoffs and each later
#: attempt is hourly — so 60 attempts ≈ 2h + 53h ≈ 2.3 days of PM-outage
#: tolerance before an entry goes terminal. ``next_attempt_at`` deferrals
#: (dependencies-not-ready) do not increment ``attempts``, so they never count.
DEFAULT_MAX_ATTEMPTS = 60

#: How long an entry may sit deferred (PENDING, ``attempts == 0``) before each
#: subsequent deferral escalates to a distinct WARNING (#15). A deps-not-ready
#: deferral keeps an entry PENDING without counting an attempt, so the
#: transport-failure cap (``DEFAULT_MAX_ATTEMPTS``) can never catch a PM
#: prerequisite that is permanently un-anchorable — it would defer forever and
#: invisibly. Escalating an old, still-never-attempted deferral to a WARNING makes
#: that stuck path operator-/alert-visible without a schema migration (it reuses
#: ``created_at``) and without touching the shared backlog read surface. 24h ≫ a
#: normal deps-ready latency (a parent anchors within a cycle or two), so an entry
#: still deferring a day later is genuinely wedged, not just briefly waiting.
DEFAULT_DEFERRED_STUCK_THRESHOLD = timedelta(hours=24)

#: Safety ceiling on the legacy ``full_list`` reconcile pagination loop (#6). The
#: full-list backstop is dead for usa-wa post-#10 (cohort producers use the bounded
#: ``anchored_cohort`` backstop, jurisdictions use ``none``) but live for siblings, so
#: a misbehaving PM that always advertises another cursor — or a non-advancing one —
#: would otherwise spin the daemon forever. Mirrors the live ``discover`` /
#: ``list_subscriptions`` page guard in ``pmclient`` (warn + break with the partial
#: set). At a typical 100 records/page this is ~100k records — orders of magnitude
#: above any bounded PM list — so it never trips normally; a runaway guard, not a knob.
MAX_RECONCILE_PAGES = 1000

#: Page size for :meth:`SyncEngine.sweep_unanchored` (#7). The sweep keyset-pages
#: the unanchored rows by primary key instead of materialising them all at once,
#: so a first bulk identity ingest (persons/orgs in the thousands) never loads the
#: whole backlog into memory per cycle. Jurisdictions (~100 rows) fit in one page.
DEFAULT_SWEEP_BATCH_SIZE = 500

#: Statuses that block re-enqueue of the same source row. PENDING is the open
#: delivery; UNAVAILABLE is a dead-letter that must not be silently re-minted by
#: the sweep (else the cap never halts retries, UNAVAILABLE rows accumulate, and
#: a redrive would collide with the fresh PENDING on ``uq_powermap_outbox_open``).
#: REJECTED is intentionally excluded: it signals a data fix, after which the next
#: sweep should re-enqueue and re-attempt the corrected row.
_REENQUEUE_BLOCKING_STATUSES = (STATUS_PENDING, STATUS_UNAVAILABLE)

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
        deferred_stuck_threshold: timedelta = DEFAULT_DEFERRED_STUCK_THRESHOLD,
        sweep_batch_size: int = DEFAULT_SWEEP_BATCH_SIZE,
    ) -> None:
        if sweep_batch_size < 1:
            raise ValueError("sweep_batch_size must be >= 1")
        self._by_type = {d.entity_type: d for d in descriptors}
        self._client = client
        self._batch_limit = batch_limit
        self._max_attempts = max_attempts
        self._deferred_stuck_threshold = deferred_stuck_threshold
        self._sweep_batch_size = sweep_batch_size
        #: Outbox ids already surfaced as deferred-stuck this process, so the WARNING
        #: fires once per wedged entry rather than every cycle (#15 throttle). Bounded
        #: by the live stuck-entry count; a daemon restart re-warns once (acceptable).
        self._warned_stuck: set = set()

    def descriptor_for(self, entity_type: str) -> EntityDescriptor | None:
        return self._by_type.get(entity_type)

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

    async def _enqueue(
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

    async def sweep_unanchored(self, session: AsyncSession, descriptor: EntityDescriptor) -> int:
        """Enqueue a CREATE for every locally-minted row with a null anchor.

        Keeps the adapter ignorant of the sidecar — it just writes rows; the
        sweep discovers the un-anchored ones and queues them.

        Batched (#7): rows are keyset-paged by primary key (``id > last_id``,
        ``sweep_batch_size`` at a time) rather than materialised all at once, so a
        first bulk identity ingest (persons/orgs in the thousands) never loads the
        whole unanchored backlog into memory in a single cycle. Keyset (not
        ``OFFSET``) is required because a CREATE leaves the anchor null until
        delivery — those rows stay in the ``anchor IS NULL`` set within the sweep,
        so advancing past the last processed id is what guarantees forward progress
        and termination instead of re-reading the same already-enqueued rows.
        """
        anchor_col = getattr(descriptor.model, descriptor.anchor_column)
        pk_col = descriptor.model.id
        enqueued = 0
        last_id = None
        while True:
            stmt = select(descriptor.model).where(anchor_col.is_(None))
            if descriptor.retired_column is not None:
                # Never re-create a retired (genuinely-deleted) row — it would
                # resurrect a deliberately-deleted entity in PM (#31).
                stmt = stmt.where(descriptor.retired_column_expr().is_(None))
            if last_id is not None:
                stmt = stmt.where(pk_col > last_id)
            stmt = stmt.order_by(pk_col).limit(self._sweep_batch_size)
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                break
            for row in rows:
                last_id = row.id
                if await self._sweep_row(session, descriptor, row):
                    enqueued += 1
            if len(rows) < self._sweep_batch_size:
                break
        return enqueued

    async def _sweep_row(self, session: AsyncSession, descriptor: EntityDescriptor, row) -> bool:
        """Process one unanchored row; return True iff a new CREATE was enqueued."""
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
                await self._maybe_enqueue_enrich(session, descriptor, record, row)
            else:
                # Matched but detail fetch failed — still capture the anchor.
                descriptor.set_anchor(row, pm_id)
            return False
        return await self._enqueue(session, descriptor, row, OP_CREATE) is not None

    async def _maybe_enqueue_enrich(
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
        Idempotent: the :meth:`_enqueue` blocking-status guard suppresses a duplicate
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
        entry = await self._enqueue(session, descriptor, row, OP_ENRICH)
        if entry is not None:
            entry.payload_hash = fingerprint
        elif identifier_missing:
            await self._upgrade_blocking_update_to_enrich(session, descriptor, row, fingerprint)

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

    # --- merge-orphan self-heal (usa-wa#31 / power-map#235) -------------------

    async def _row_by_anchor(
        self, session: AsyncSession, descriptor: EntityDescriptor, pm_id: Any
    ) -> Any | None:
        """The local row anchored to ``pm_id``, or None. Generic by-anchor lookup so
        the feed's ``deleted`` branch can find a row from a bare entity id (no record)."""
        if pm_id is None:
            return None
        return await session.scalar(
            select(descriptor.model).where(descriptor.anchor_column_expr() == as_ulid(pm_id))
        )

    async def _heal_dead_anchor(
        self, session: AsyncSession, descriptor: EntityDescriptor, row, *, now: datetime
    ) -> None:
        """Heal a row whose PM anchor is dead — PM merged the entity away (a 404 on
        re-fetch, or a ``deleted`` feed event) and our anchor points at the deleted
        loser. Re-resolve the surviving winner by identifier and re-anchor + re-enrich
        (the carry fields the winner lacks re-push via #34 drift). A descriptor that
        cannot re-match is left untouched and logged (never wrongly retired); one that
        can but finds no winner is a genuine delete → retire locally.

        Idempotent: a row already re-anchored to a live winner won't 404 again, and a
        retired row is excluded from the sweep/reconcile that would re-encounter it.
        """
        if not descriptor.supports_rematch:
            logger.warning(
                "dead_anchor_unhealed",
                extra={
                    "entity_type": descriptor.entity_type,
                    "anchor": str(descriptor.anchor_value(row)),
                },
            )
            return
        winner = await descriptor.rematch_anchor(self._client, session, row)
        if winner is None:
            # No surviving identifier winner → genuine delete (or a merge that didn't
            # transfer identifiers — power-map#235 will disambiguate). Retire, loudly.
            descriptor.retire(row, now)
            await session.flush()
            logger.warning(
                "dead_anchor_retired",
                extra={"entity_type": descriptor.entity_type, "local_id": str(row.id)},
            )
            return
        old = descriptor.anchor_value(row)
        descriptor.set_anchor(row, winner)
        record = await descriptor.fetch_record(self._client, winner)
        if record is not None:
            await self.apply_record(session, descriptor, record)
            await self._maybe_enqueue_enrich(session, descriptor, record, row, check_drift=True)
        await session.flush()
        logger.info(
            "dead_anchor_reanchored",
            extra={
                "entity_type": descriptor.entity_type,
                "local_id": str(row.id),
                "old_anchor": str(old),
                "winner": str(winner),
            },
        )

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
        touched: list[OutboxEntry] = []
        since_commit = 0
        for entry in await self._due_entries(session, now):
            descriptor = self.descriptor_for(entry.entity_type)
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
        try:
            result = await self._client.post_observation(descriptor.observe_path, payload)
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
            await self._reject(session, entry, str(exc))
            return True

        entry.last_disposition = result.disposition
        if result.anchored:
            descriptor.set_anchor(row, result.pm_id)
            entry.status = STATUS_DELIVERED
            entry.last_error = None
            await self._stamp_enrich_fingerprint(session, entry)
        elif result.rejected:
            await self._reject(session, entry, str(result.raw), raw=result.raw)
        else:
            # Unexpected disposition — count it as a failed attempt so an operator
            # can see it and it cannot loop forever.
            self._fail_attempt(entry, now, f"unexpected disposition: {result.disposition!r}")
        return True

    async def _reject(
        self, session: AsyncSession, entry: OutboxEntry, error: str, *, raw: dict | None = None
    ) -> None:
        """Park an entry to the ``REJECTED`` terminal state (PM refused the payload).

        Shared by the ``rejected`` disposition path and the permanent payload-error
        path. ``REJECTED`` is re-sweepable: once the data is fixed, the next sweep
        re-enqueues the corrected row.

        For an ENRICH it also stamps the fingerprint (#34): PM gave a terminal verdict
        on this exact payload, so the reconcile must not re-post the identical payload
        every cycle. A subsequent data/code fix changes the payload hash, which re-arms
        the drift trigger — so a rejection self-heals on the fix, not by blind retry.
        """
        entry.status = STATUS_REJECTED
        entry.last_error = error
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
        is_stuck = age is not None and age >= self._deferred_stuck_threshold
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
        if entry.attempts >= self._max_attempts:
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

    # --- read path: reconcile backstops --------------------------------------

    async def reconcile(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        *,
        now: datetime | None = None,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> int:
        """Run the descriptor's reconcile backstop, dispatched by ``reconcile_mode``.

        A reconcile is the periodic drift-recovery backstop, never the primary read.
        Which backstop runs is a first-class axis (CannObserv/usa-wa#13):

        - ``none`` → no backstop (the feed + subscription/discovery path is the only
          refresh). Returns 0.
        - ``full_list`` → :meth:`_reconcile_full_list`: full enumeration of
          ``read_path``. Legacy; sibling-only post-#10 (no usa-wa descriptor uses it).
        - ``anchored_cohort`` → :meth:`_reconcile_anchored_cohort`: re-fetch only OUR
          anchored rows by id, recovering a curation edit whose feed event was dropped.

        Both backstops stamp ``reconcile:<entity_type>`` with ``now`` (when given) so
        the sidecar cadence gate sees the run.

        Transaction boundary (#13 CR): like :meth:`drain_outbox`, each page makes PM
        network round-trips. When a ``commit`` callback is supplied the backstop
        commits after every page, so a large cohort (or sibling list) never holds one
        open transaction across all of them. With no callback the legacy
        single-transaction behaviour is preserved (the caller owns the commit).
        """
        if descriptor.read_source == "none" or descriptor.read_path is None:
            return 0
        if descriptor.reconcile_mode == "full_list":
            applied = await self._reconcile_full_list(session, descriptor, commit=commit)
        elif descriptor.reconcile_mode == "anchored_cohort":
            applied = await self._reconcile_anchored_cohort(
                session, descriptor, now=now, commit=commit
            )
        else:  # "none"
            return 0
        if now is not None:
            state = await self._get_or_create_state(session, _reconcile_stream(descriptor))
            state.last_reconcile_at = now
        await session.flush()
        return applied

    async def _reconcile_full_list(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        *,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> int:
        """Page the entity's PM list endpoint, applying every record under LWW.

        The legacy backstop, sibling-only post-#10 (no usa-wa descriptor runs it).

        Bounded (#6): a misbehaving PM that never stops advertising a cursor (or a
        non-advancing one) must not spin this loop forever. Since the full-list
        reconcile is live for siblings, the same warn-and-break max-page guard the
        live ``discover`` / ``list_subscriptions`` loops use applies here too — on
        exceed: warn + break with whatever was applied so far.

        Commits per page when ``commit`` is supplied (see :meth:`reconcile`).
        """
        applied = 0
        cursor: str | None = None
        for _page in range(MAX_RECONCILE_PAGES):
            params = {"cursor": cursor} if cursor else None
            page = await self._client.list_entities(descriptor.read_path, params)
            for record in page.records:
                await self.apply_record(session, descriptor, record)
                applied += 1
            if commit is not None:
                await session.flush()
                await commit()
            cursor = page.cursor
            if not cursor:
                return applied
        logger.warning(
            "reconcile_pagination_bound_exceeded",
            extra={
                "entity_type": descriptor.entity_type,
                "max_pages": MAX_RECONCILE_PAGES,
                "applied": applied,
            },
        )
        return applied

    async def _reconcile_anchored_cohort(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        *,
        now: datetime | None = None,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> int:
        """Re-fetch only OUR anchored rows by id and re-apply each under LWW (#13).

        The bounded backstop for cohort-only producers (orgs/persons/roles/
        assignments). It selects local rows whose anchor ``IS NOT NULL`` — the cohort
        WE produced and PM now curates — NOT PM's global list, so it is O(our cohort),
        never O(PM-world). For each it ``GET``s the current PM record by the stored
        anchor id and applies it through the LWW :meth:`apply_record` path, so a
        curation edit whose feed event was dropped is recovered (and a row that is
        already current is a no-op via LWW parity).

        Keyset-paged by primary key (``sweep_batch_size`` at a time), mirroring
        :meth:`sweep_unanchored`, so a large anchored cohort never materialises all at
        once. Unlike the sweep, the anchor is *not* mutated here, so the
        ``anchor IS NOT NULL`` set is stable across pages — but keyset paging by PK
        still gives a deterministic, terminating walk.

        Re-enrich (#34) is evaluated here, not on the changes-feed apply path: this is
        the single place that re-derives the carry payload for the whole anchored
        cohort, so a held-identifier change or carry-field drift self-heals on the
        reconcile cadence (hourly) rather than per feed event. A feed bump alone does
        not re-enrich — it defers to this backstop (see :meth:`_maybe_enqueue_enrich`).
        """
        anchor_col = descriptor.anchor_column_expr()
        pk_col = descriptor.model.id
        applied = 0
        last_id = None
        while True:
            stmt = select(descriptor.model).where(anchor_col.is_not(None))
            if descriptor.retired_column is not None:
                # Skip retired (genuinely-deleted) rows — never re-fetch a tombstoned id.
                stmt = stmt.where(descriptor.retired_column_expr().is_(None))
            if last_id is not None:
                stmt = stmt.where(pk_col > last_id)
            stmt = stmt.order_by(pk_col).limit(self._sweep_batch_size)
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                break
            for row in rows:
                last_id = row.id
                pm_id = descriptor.anchor_value(row)
                record = await descriptor.fetch_record(self._client, pm_id)
                if record is None:
                    # PM record gone (404): the entity was merged/deleted. Self-heal —
                    # re-anchor to the merge-winner, or retire on a genuine delete (#31).
                    await self._heal_dead_anchor(session, descriptor, row, now=now or _utcnow())
                    continue
                await self.apply_record(session, descriptor, record)
                # Re-evaluate enrichment for the anchored row (#34): a held identifier
                # (trigger gap) or a drifted carry payload (detection gap, check_drift)
                # re-enqueues an ENRICH here rather than waiting on a manual backfill.
                await self._maybe_enqueue_enrich(session, descriptor, record, row, check_drift=True)
                applied += 1
            if commit is not None:
                # Bound the open transaction to one page of PM round-trips (#13 CR).
                await session.flush()
                await commit()
            if len(rows) < self._sweep_batch_size:
                break
        return applied

    # --- read path: changes feed (incremental primary for person/org) --------

    async def process_feed(
        self, session: AsyncSession, *, now: datetime | None = None, limit: int = 100
    ) -> int:
        """Pull one batch of changes, apply them, and advance the cursor.

        The feed yields ``(entity_type, id, change_kind)`` only, so each change
        is resolved to a full record via :meth:`PowerMapClient.get_entity`
        before upsert. A ``deleted`` event is the timely merge-orphan signal: if it
        names a row we anchored, route it to :meth:`_heal_dead_anchor` (re-anchor to
        the merge-winner, or retire on a genuine delete, #31); a delete for an entity
        we never produced is still skipped. ``now`` stamps any retirement (threaded
        from the sidecar tick; falls back to wall clock for ad-hoc callers).

        Read-path scope note: a permanent client error here (the typed
        :class:`DeliveryBlockedError` / :class:`PayloadRejectedError`, e.g. a
        mis-scoped read key) is intentionally *not* caught — there is no per-entry
        "park" for reads, and a read PM can't make forward progress at all if its
        credential is rejected, so the error propagates and the per-cycle isolation
        rolls back + logs the cycle. Only the write path (:meth:`_deliver`) parks
        permanent failures, because there a single poison entry must not starve the
        rest of the outbox.
        """
        state = await self._get_or_create_state(session, CHANGES_STREAM)
        after = _parse_after(state.cursor)
        page = await self._client.get_changes(after, limit=limit)
        applied = 0
        for item in page.items:
            descriptor = self.descriptor_for(item.entity_type)
            if descriptor is None or descriptor.read_source == "none":
                continue
            if item.change_kind == "deleted":
                row = await self._row_by_anchor(session, descriptor, item.entity_id)
                if row is not None and not descriptor.is_retired(row):
                    await self._heal_dead_anchor(session, descriptor, row, now=now or _utcnow())
                continue
            record = await descriptor.fetch_record(self._client, item.entity_id)
            if record is None:
                continue
            await self.apply_record(session, descriptor, record)
            applied += 1
        if page.next_after is not None:
            state.cursor = str(page.next_after)
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


def _parse_after(cursor: str | None) -> int | None:
    """Parse the stored ``changes_feed`` cursor into the integer ``after`` seq.

    The PM #203 cutover replaced the timestamp cursor with an outbox seq_id. A
    stored value left over from the old timestamp scheme (or any non-integer) is
    not a valid ``after`` — treat it as "from the start" (0) and log once rather
    than crash the feed. ``None`` (fresh stream) is passed through to mean seq 0.
    """
    if cursor is None:
        return None
    try:
        return int(cursor)
    except ValueError:
        logger.warning("powermap_feed_cursor_reset", extra={"stale_cursor": cursor})
        return 0
