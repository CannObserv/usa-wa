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

  DEAD-ANCHOR self-heal (usa-wa#31/#36/#37) — a PM-side merge deletes the loser and
  keeps the winner, orphaning our anchor. Both read paths detect it and route to
  :meth:`_heal_dead_anchor`: ``process_feed`` on a ``deleted`` event (the timely
  signal) and ``_reconcile_anchored_cohort`` on a re-fetch 404 (the backstop). The
  winner is resolved from one of two signals, in order of trust:

  - PM's explicit ``merged_into`` on the ``deleted`` event (power-map#235, consumed in
    usa-wa#37) — deterministic, so it re-anchors *any* entity type generically with no
    identifier re-match.
  - identifier re-match — the backstop when no ``merged_into`` was seen (a 404, or a
    bare ``deleted`` for a rematch-capable org). Only the org descriptor supports it.

  A bare ``deleted`` (no ``merged_into``) is otherwise an unambiguous genuine delete
  post-power-map#235: non-rematch types (person/role/assignment) delete (``deleted_at``).
  The heal also retires a duplicate orphan when a many-to-one merge already left another
  local row on the winner, and a non-rematch type with no winner signal at all (a 404
  backstop) logs once and leaves the row. Retired rows are excluded from the sweep and
  reconcile.

Write-path drain detail (:meth:`drain_outbox`):
    post observations, settle dispositions, back off on transient error,
    dead-letter to UNAVAILABLE once the retry cap is exhausted (or immediately on
    a permanent auth/scope block), and park to REJECTED on a permanent payload
    refusal — a poison entry parks itself rather than rolling back the whole cycle.
"""

import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import ColumnElement, case, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.client import (
    DeliveryBlockedError,
    ObservationResult,
    PayloadRejectedError,
    PowerMapClient,
    RetryableClientError,
)
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid
from clearinghouse_sync_powermap.models import (
    DISPOSITION_AUTO_ATTACHED,
    OP_CREATE,
    OP_ENRICH,
    OP_UPDATE,
    STATUS_DELIVERED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    AnchorReanchor,
    EnrichFingerprint,
    NonConvergenceState,
    OutboxEntry,
    SyncState,
)
from clearinghouse_sync_powermap.retry import next_attempt_at

logger = get_logger(__name__)

#: SyncState stream key for the shared PM changes feed.
CHANGES_STREAM = "changes_feed"


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

#: Consecutive identical ``auto-attached`` re-sends of an already-anchored row before
#: the non-convergence backstop flags it (usa-wa#112). Re-enqueue is reconcile-gated
#: (``RECONCILE_CADENCE`` default 12h; PM auto-attaches without advancing its clock so
#: the feed never re-fires — #109), so 3 ≈ 1.5 days of proven-futile churn before an
#: operator-visible flag. Configurable via ``SidecarSettings.nonconvergence_threshold``.
DEFAULT_NONCONVERGENCE_THRESHOLD = 3

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

#: Foreground backoff schedule for transient reads inside the anchored-cohort
#: crawl (usa-wa#85) — pause-and-resume, not cycle-abort: a 429 mid-crawl used to
#: propagate, roll back the reconcile stamp, and trigger an immediate full re-crawl
#: (the #88 miniature of the #84 loop). Small like validate_committees' schedule
#: (not the 60s-base outbox one) so a transient blip doesn't stall the cycle; the
#: length is the per-read retry budget — exhausting it re-raises into the sidecar's
#: per-descriptor boundary. A server ``Retry-After`` hint overrides a step.
READ_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0)


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
    the start of every :meth:`SyncEngine.drain_outbox` so it reflects one drain only.
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
    #: separately (:func:`nonconverging_count`) for the cycle summary + rise alert.
    non_converging: int = 0


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
        nonconvergence_threshold: int = DEFAULT_NONCONVERGENCE_THRESHOLD,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if sweep_batch_size < 1:
            raise ValueError("sweep_batch_size must be >= 1")
        if nonconvergence_threshold < 1:
            # A 0/negative threshold flags on the first stable re-observe AND makes
            # ``nonconverging_count``'s ``count >= threshold`` match every *reset* (count 0)
            # row — inverting the standing query into "the whole cohort" and turning the
            # rise-alert into a per-cycle flood naming converged rows (#112 CR-1).
            raise ValueError("nonconvergence_threshold must be >= 1")
        self._by_type = {d.entity_type: d for d in descriptors}
        #: Drain priority per entity type = its index in the (dependency-first)
        #: descriptor registry order. Lower drains first, so a dependency **root**
        #: (org/role) is always attempted before its dependents (assignments) in a
        #: single batch — the fix for the #96 bulk-produce starvation, where frozen
        #: role roots were crowded out of the ``next_attempt_at``-only ``LIMIT`` cut
        #: by thousands of dependency-blocked assignments deferred just ahead of them.
        #: ``build_descriptors`` authors this order; it is load-bearing here, not
        #: merely informational.
        self._drain_priority = {d.entity_type: i for i, d in enumerate(descriptors)}
        self._client = client
        self._batch_limit = batch_limit
        self._max_attempts = max_attempts
        self._deferred_stuck_threshold = deferred_stuck_threshold
        self._nonconvergence_threshold = nonconvergence_threshold
        self._sweep_batch_size = sweep_batch_size
        # Injectable for the transient-read retry tests (usa-wa#85); production sleeps.
        self._sleep = sleep
        #: Outbox ids already surfaced as deferred-stuck this process, so the WARNING
        #: fires once per wedged entry rather than every cycle (#15 throttle). Bounded
        #: by the live stuck-entry count; a daemon restart re-warns once (acceptable).
        self._warned_stuck: set = set()
        #: Local row ids already surfaced as an unhealable dead anchor this process, so
        #: a descriptor that can't re-match (person/role/assignment until power-map#235)
        #: warns once per wedged row rather than every reconcile cycle (#36). Same
        #: throttle shape as ``_warned_stuck``; a restart re-warns once (acceptable).
        self._warned_dead_anchors: set = set()
        #: Local row ids already surfaced as non-converging this process (#112 CR-3). A
        #: flagged row re-flags on EVERY drain (the churn is by definition repeating), so
        #: without this the #110-sized cohort (305 rows) would emit 305 WARNINGs per drain
        #: and bury the signal. Same throttle shape as ``_warned_stuck``: WARNING once per
        #: row, INFO thereafter; a restart re-warns once (acceptable). The per-drain
        #: ``DrainStats.non_converging`` tally and the standing count stay unthrottled.
        self._warned_nonconverging: set = set()
        #: Per-drain observability tallies (usa-wa#108), reset at each drain start and
        #: read by the sidecar's cycle summary. Defaults so a caller that reads it before
        #: any drain gets an empty, safe value.
        self._last_drain_stats = DrainStats()

    @property
    def last_drain_stats(self) -> DrainStats:
        """Disposition + re-anchor tallies from the most recent :meth:`drain_outbox`."""
        return self._last_drain_stats

    def descriptor_for(self, entity_type: str) -> EntityDescriptor | None:
        return self._by_type.get(entity_type)

    @property
    def descriptors(self) -> tuple[EntityDescriptor, ...]:
        """All registered descriptors (read-only). Lets membership managers (e.g. the
        subscription reconciler's local-cohort discovery) enumerate the entity set."""
        return tuple(self._by_type.values())

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

        Batched (#7): rows are keyset-paged by primary key (``id > last_id``,
        ``sweep_batch_size`` at a time) rather than materialised all at once, so a
        first bulk identity ingest (persons/orgs in the thousands) never loads the
        whole unanchored backlog into memory in a single cycle. Keyset (not
        ``OFFSET``) is required because a CREATE leaves the anchor null until
        delivery — those rows stay in the ``anchor IS NULL`` set within the sweep,
        so advancing past the last processed id is what guarantees forward progress
        and termination instead of re-reading the same already-enqueued rows.

        When ``commit`` is supplied the sweep commits **per batch** (#92), mirroring
        :meth:`_crawl_and_apply`: a first bulk ingest runs one PM match per row, so
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
        # and ``_enqueue`` no-ops on the same guard anyway. Correlated NOT EXISTS on the
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
            stmt = stmt.order_by(pk_col).limit(self._sweep_batch_size)
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                break
            for row in rows:
                last_id = row.id
                if await self._sweep_row(session, descriptor, row):
                    enqueued += 1
            if commit is not None:
                # Bound the open transaction to one page of PM round-trips + persist
                # each batch's progress before the next (#92, mirroring _crawl_and_apply).
                await session.flush()
                await commit()
            if len(rows) < self._sweep_batch_size:
                break
        return enqueued

    async def _sweep_row(self, session: AsyncSession, descriptor: EntityDescriptor, row) -> bool:
        """Process one unanchored row; return True iff a new CREATE was enqueued."""
        # PM-first: try to find a pre-existing PM record before creating one,
        # so we never duplicate PM's curated tree (identifier-less backfill). The match
        # is a PM read, so it pauses-and-resumes on a 429 (#92) — a first bulk ingest
        # runs one search per un-anchored row, exactly the burst that trips PM's limit;
        # a bare 429 here would abort the whole tick and lose the batch's progress.
        pm_id = await self._read_with_retry(
            lambda: descriptor.pm_match(self._client, session, row),
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
        if pm_id is not None and not await self._anchor_taken(
            session, descriptor, row, pm_id, log=False
        ):
            record = await self._fetch_record_with_retry(descriptor, pm_id)
            if record is not None:
                # Adopt PM's canonical fields + anchor; no create.
                await descriptor.upsert_from_pm(session, record, existing=row)
                self._adopt_remote_clock(descriptor, row, record)
                # Enrich-on-match (#198): PM matched an identifier-less record by
                # name — push our identifiers/names onto it so it gains the data
                # we hold and future syncs match by identifier.
                await self._maybe_enqueue_enrich(session, descriptor, record, row)
            else:
                # Matched but detail fetch failed — still capture the anchor (clock
                # preserved: this row is not yet synced, so it must not read newer).
                self._stamp_anchor(descriptor, row, pm_id)
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
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        row,
        *,
        now: datetime,
        winner_hint: ULID | None = None,
    ) -> None:
        """Heal a row whose PM anchor is dead — PM merged the entity away (a 404 on
        re-fetch, or a ``deleted`` feed event) and our anchor points at the deleted
        loser. Re-anchor to the surviving winner + re-enrich (the carry fields the
        winner lacks re-push via #34 drift).

        The winner comes from one of two signals, in order of trust:

        - ``winner_hint`` — PM's explicit ``merged_into`` on the ``deleted`` feed event
          (power-map#235 / usa-wa#37). Deterministic, so it heals *any* entity type
          generically — no identifier re-match, no ``supports_rematch`` gate.
        - identifier re-match — the backstop signal, when no ``merged_into`` was seen:
          a re-fetch 404, *or* a bare ``deleted`` feed event carrying no ``merged_into``
          for a rematch-capable descriptor. Only the org descriptor supports it; a
          descriptor that can't re-match is left untouched and logged once (never
          wrongly retired), and one that can but finds no winner retires locally.

        Idempotent: a row already re-anchored to a live winner won't 404 again, and a
        retired row is excluded from the sweep/reconcile that would re-encounter it.
        """
        log_ctx = {"entity_type": descriptor.entity_type, "local_id": str(row.id)}
        if winner_hint is not None:
            winner: ULID | None = winner_hint
        elif not descriptor.supports_rematch and descriptor.is_archived(row):
            # The row was already archived in PM and now 404s. PM enforces
            # archive-before-hard-delete (409 unless ``archived_at`` is set), so a 404
            # on an *archived* id is a settled genuine delete, not an ambiguous merge —
            # promote archived → deleted even without identifier re-match. This also
            # stops the row 404ing on every reconcile cycle (it was kept in the cohort
            # by the archived axis, usa-wa#42).
            descriptor.mark_deleted(row, now)
            await session.flush()
            logger.info("dead_anchor_deleted_from_archived", extra=log_ctx)
            return
        elif not descriptor.supports_rematch:
            # No explicit winner and can't resolve one by identifier (person/role/
            # assignment on the 404 backstop — the feed path carries merged_into and
            # never reaches here). Leave the row — retiring a possibly-merged entity
            # with no signal would be wrong — and warn ONCE per row this process
            # (#36 CR), not every cycle.
            if row.id not in self._warned_dead_anchors:
                self._warned_dead_anchors.add(row.id)
                logger.warning(
                    "dead_anchor_unhealed",
                    extra={**log_ctx, "anchor": str(descriptor.anchor_value(row))},
                )
            return
        else:
            winner = await descriptor.rematch_anchor(self._client, session, row)
            if winner is None:
                # No surviving identifier winner → genuine delete (or a merge that
                # didn't transfer identifiers). Retire, loudly. (The feed path settles
                # this deterministically via merged_into; this is only the 404 backstop.)
                descriptor.mark_deleted(row, now)
                await session.flush()
                logger.warning("dead_anchor_deleted", extra=log_ctx)
                return
        # Many-to-one merge guard (#36 CR finding 1): if another local row already
        # anchors to the winner, PM merged two of our rows into one canonical entity.
        # Re-pointing this row too would mint a duplicate anchor (and crash the next
        # anchor-keyed local_match). The winner is already represented → retire this
        # orphan instead. We do NOT re-push the orphan's carry evidence (label/acronym)
        # to the winner: PM's merge already carried both rows' contacts + identifiers
        # onto the winner (both ids land there — that's why both rematch to it), so the
        # winner is already complete; the other row's re-anchor pushes its own evidence.
        holder = await self._row_by_anchor(session, descriptor, winner)
        if holder is not None and holder.id != row.id:
            descriptor.mark_deleted(row, now)
            await session.flush()
            logger.warning(
                "dead_anchor_deleted_duplicate_winner",
                extra={**log_ctx, "winner": str(winner), "kept_local_id": str(holder.id)},
            )
            return
        old = descriptor.anchor_value(row)
        # Clock-preserving (CR-1): when the winner's detail fetch 404s (a merge chain)
        # no apply_record follows to adopt PM's clock, so a bumped clock would strand
        # this row local-newer against the winner.
        self._stamp_anchor(descriptor, row, winner)
        # fetch_record can 404 here if the named winner was itself later merged away (a
        # merge chain). We've re-anchored to it regardless, so the row is briefly a fresh
        # dead anchor — harmless: the next feed deleted(winner) / reconcile 404 re-heals
        # it to the final winner. We only adopt canonical fields + re-enrich when the
        # winner resolves (CR #7).
        record = await descriptor.fetch_record(self._client, winner)
        if record is not None:
            await self.apply_record(session, descriptor, record)
            await self._maybe_enqueue_enrich(session, descriptor, record, row, check_drift=True)
        await session.flush()
        logger.info(
            "dead_anchor_reanchored",
            extra={**log_ctx, "old_anchor": str(old), "winner": str(winner)},
        )

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
        # the cut forever (attempts frozen). ``_drain_priority`` maps each entity
        # type to its registry index; an unknown type (no descriptor) sorts last.
        # ``id`` is the final tiebreak so same-tier, same-``next_attempt_at`` entries
        # (a bulk produce clusters thousands) order stably cycle-to-cycle rather than
        # by nondeterministic physical order — the ULID id also roughly encodes
        # enqueue time, so the tiebreak is FIFO-ish within the tie.
        order_by: list[Any] = []
        if self._drain_priority:  # case({}) is illegal; empty registry drains nothing anyway
            order_by.append(
                case(
                    self._drain_priority,
                    value=OutboxEntry.entity_type,
                    else_=len(self._drain_priority),
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
        self._last_drain_stats = DrainStats()  # per-drain tallies (usa-wa#108)
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
            if await self._anchor_taken(session, descriptor, row, result.pm_id):
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
            await self._record_reanchor(session, descriptor, row, result)
            self._stamp_anchor(descriptor, row, result.pm_id)
            entry.status = STATUS_DELIVERED
            entry.last_error = None
            await self._stamp_enrich_fingerprint(session, entry)
            await self._track_convergence(
                session, descriptor, row, entry, result, old_anchor, payload
            )
        elif result.rejected:
            await self._reject(session, entry, str(result.raw), raw=result.raw)
        else:
            # Unexpected disposition — count it as a failed attempt so an operator
            # can see it and it cannot loop forever.
            self._fail_attempt(entry, now, f"unexpected disposition: {result.disposition!r}")
        return True

    async def _record_reanchor(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        row: Any,
        result: ObservationResult,
    ) -> None:
        """Capture an in-place anchor **overwrite** before it destroys the old id (#108).

        PM dedups assignments on ``(person, role, start_date)``, so a producer's
        start-date correction re-produces an observation that no longer matches the
        stored key: PM mints a *fresh* assignment (disposition ``new``) and returns its
        id, while the assignment our anchor still points at is silently orphaned upstream.
        :meth:`_stamp_anchor` then overwrites ``pm_*_id`` in place — so the old id, the
        only handle on the orphan, is gone the instant the stamp lands.

        This runs *before* that stamp whenever the delivered id differs from the anchor
        the row already carries, and does two things the overwrite would otherwise lose:
        a WARNING (the alert) and a durable :class:`AnchorReanchor` ledger row (the
        queryable, retained record the orphan-reconcile cleanup reads once power-map#311
        ships). A first-time stamp (row had no anchor — an ordinary CREATE) is not an
        overwrite and is skipped; a re-delivery returning the *same* id is a no-op.

        Generic across entity types: any anchored row re-resolving to a different id is
        an orphan-minting overwrite, whatever the match semantics that caused it.
        """
        old = descriptor.anchor_value(row)
        if old is None or old == result.pm_id:
            return  # first anchor, or an unchanged re-observe — no orphan
        source_id = getattr(row, "source_id", None)
        logger.warning(
            "anchor_reanchored",
            extra={
                "entity_type": descriptor.entity_type,
                "local_id": str(row.id),
                "source_id": source_id,
                "old_pm_id": str(old),
                "new_pm_id": str(result.pm_id),
                "disposition": result.disposition,
            },
        )
        session.add(
            AnchorReanchor(
                entity_type=descriptor.entity_type,
                local_id=row.id,
                source_id=source_id,
                old_pm_id=old,
                new_pm_id=result.pm_id,
                disposition=result.disposition,
            )
        )
        self._last_drain_stats.reanchors += 1

    async def _track_convergence(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        row: Any,
        entry: OutboxEntry,
        result: ObservationResult,
        old_anchor: Any,
        payload: dict,
    ) -> None:
        """Accrue/reset the per-row consecutive-identical-``auto-attached`` counter (#112).

        The generic non-convergence backstop. An anchored row whose reconcile
        re-observation PM keeps ``auto-attached`` *without applying our diff* (the #110
        role-classifier churn, power-map#311b before #111) re-sends an identical payload
        every reconcile cycle forever — silent until a manual outbox audit. The per-cohort
        no-op gates (#102/#104/#109) only catch pure clock skew, not a genuine local↔PM
        diff PM refuses. This converts the silent churn into an operator-visible, alerting
        standing count (:class:`~clearinghouse_sync_powermap.models.NonConvergenceState`).

        Accrues ONLY on a *stable re-observe*: disposition ``auto-attached`` **and** the row
        was already anchored to this same id (``old_anchor == result.pm_id``). A re-anchor
        (``old != pm_id``) is a #108 genuine change and a ``new`` disposition mints a record —
        both converge the row and reset it. A **changed** ``payload_hash`` also resets to 1
        (the re-arm — a real new local edit still propagates; only the provably-futile
        identical re-send is caught).

        A first attach (``old_anchor is None``) returns **before** the state query (#112
        CR-2): it can neither accrue (it is not a re-send) nor have prior state to reset (a
        state row is only ever written for an anchored row), and the bulk-produce CREATE path
        delivers thousands of such rows — paying a SELECT + an extra autoflush on each would
        regress a path deliberately tuned by #92/#93/#96.

        Both ``OP_UPDATE`` and ``OP_ENRICH`` can climb. An enrich re-enqueues whenever
        :meth:`_maybe_enqueue_enrich` sees ``identifier_missing`` — that trigger is *not*
        fingerprint-gated, so a row whose identifier PM persistently fails to adopt re-sends
        an identical enrich payload every reconcile and is flagged. That is intended coverage,
        not a false positive: it is a genuine non-convergence. A *drift*-triggered enrich
        cannot climb, since a changed payload resets the counter by construction.

        Detection-only (usa-wa#112 Phase A): the row keeps delivering, so no ``UNAVAILABLE``
        park and no false-park risk — the re-POST cost is already bounded by the PM
        min-interval governor (#85) and the 12h reconcile cadence, and the harm the #110
        audit found was *silence*, not cost. A park + enqueue-side re-arm is a deferred
        Phase B, warranted only if the standing count ever shows a cohort large enough for
        the governed cost to matter.
        """
        if old_anchor is None:
            # First attach — nothing to accrue, and no state row can exist yet. Return before
            # the query so the bulk-produce CREATE path pays neither a SELECT nor an autoflush.
            return
        stable_reobserve = (
            result.disposition == DISPOSITION_AUTO_ATTACHED and old_anchor == result.pm_id
        )
        state = await session.scalar(
            select(NonConvergenceState).where(
                NonConvergenceState.entity_type == descriptor.entity_type,
                NonConvergenceState.local_id == row.id,
            )
        )
        if not stable_reobserve:
            # A genuine change converged the row — clear any prior non-convergence so a
            # later real edit that PM again refuses starts a fresh count.
            if state is not None and state.count != 0:
                state.count = 0
                state.payload_hash = None
                self._rearm_nonconverging(row)
            return
        fingerprint = enrich_fingerprint(payload)
        if state is None:
            state = NonConvergenceState(
                entity_type=descriptor.entity_type,
                local_id=row.id,
                payload_hash=fingerprint,
                count=1,
            )
            session.add(state)
        elif state.payload_hash == fingerprint:
            state.count += 1
        else:
            # Changed payload = a genuine new local edit (the re-arm): reset + re-baseline.
            state.payload_hash = fingerprint
            state.count = 1
            self._rearm_nonconverging(row)
        if state.count < self._nonconvergence_threshold:
            return
        self._last_drain_stats.non_converging += 1
        extra = {
            "entity_type": descriptor.entity_type,
            "local_id": str(row.id),
            "source_id": getattr(row, "source_id", None),
            "pm_id": str(result.pm_id),
            "consecutive": state.count,
            "op": entry.op,
            "disposition": result.disposition,
        }
        # Throttled per row per process (#112 CR-3): the churn repeats by definition, so a
        # flagged row would otherwise WARN on every drain — 305 lines a drain for a
        # #110-sized cohort. One actionable WARNING, then INFO. The rise-alert and the
        # standing count (both unthrottled) remain the always-visible operator surface.
        if row.id in self._warned_nonconverging:
            logger.info("observation_still_not_converging", extra=extra)
            return
        self._warned_nonconverging.add(row.id)
        logger.warning("observation_not_converging", extra=extra)

    def _rearm_nonconverging(self, row: Any) -> None:
        """Re-arm the per-row WARNING throttle when a row's counter resets (#112 CR-9).

        Unlike ``_warned_stuck`` — whose subject genuinely cannot recover, so warning once
        per process is the whole point — a non-convergence *can* clear and recur: the
        operator fixes the diff, the payload changes, the counter resets. A second episode
        is a genuinely new event, and the standing count already treats it as one (it drops
        to 0 and the next rise re-alerts). Without this discard the throttle would keep that
        second episode at INFO, so the rise email would tell the operator to grep for a
        ``observation_not_converging`` line that only exists from the *first* episode, with
        a misleading timestamp. Keeping the alert and its evidence in agreement is the point.
        """
        self._warned_nonconverging.discard(row.id)

    async def _anchor_taken(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        row: Any,
        pm_id: ULID,
        *,
        log: bool = True,
    ) -> bool:
        """Whether a **different** local row already carries this PM anchor.

        The write-side guard for the one-row-per-anchor invariant (usa-wa#86). PM
        dedups observations on ``(person, role, start_date)`` and returns an existing
        id, so two local rows can resolve to one PM assignment; stamping the second
        would violate the anchor's partial unique index and — uncaught — abort the
        whole tick, spinning the cycle (the fast-loop counterpart of the #84 slow
        reconcile loop). Both anchor-stamp sites consult this: the drain delivery
        (:meth:`_deliver`) parks the offending entry, and the sweep's PM-first
        adoption (:meth:`_sweep_row`) declines the adopt and falls through to a CREATE
        so the drain owns the single park. Autoflush makes a same-transaction
        sibling's pending anchor visible here, so two rows delivered in one drain are
        caught too. The DB index remains the hard backstop for any writer this
        single-drainer check misses.

        ``log=False`` suppresses the ``anchor_invariant_violation`` line so a caller
        that re-checks every cycle (the sweep) does not spam it — the authoritative
        one is emitted where the row is parked (the drain).
        """
        conflict = (
            await session.execute(
                select(descriptor.anchor_column_expr())
                .where(descriptor.anchor_column_expr() == pm_id, descriptor.model.id != row.id)
                .limit(1)
            )
        ).first()
        if conflict is not None and log:
            logger.error(
                "anchor_invariant_violation",
                extra={
                    "entity_type": descriptor.entity_type,
                    "anchor_column": descriptor.anchor_column,
                    "pm_id": str(pm_id),
                    "local_id": str(row.id),
                },
            )
        return conflict is not None

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
                    # The row is legitimately newer than PM here, so this is not a skew —
                    # but capturing the anchor must not inflate its clock further either.
                    self._stamp_anchor(descriptor, existing, pm_id)
            if descriptor.write_enabled:
                if await descriptor.local_newer_is_noop(session, existing, record):
                    # #102: local is "newer" only by a spurious clock skew — re-producing this
                    # row would not change PM. Adopt PM's clock (parity) instead of enqueuing an
                    # identical observation the reconcile would re-send every cycle forever.
                    self._adopt_remote_clock(descriptor, existing, record)
                else:
                    await self._enqueue(session, descriptor, existing, OP_UPDATE)
            return APPLY_KEPT_LOCAL

        row = await descriptor.upsert_from_pm(session, record, existing=existing)
        self._adopt_remote_clock(descriptor, row, record)
        return APPLY_UPDATED

    def _stamp_anchor(self, descriptor: EntityDescriptor, row: object, pm_id: Any) -> None:
        """Stamp the PM anchor onto ``row`` **without letting the flush bump its clock**.

        ``set_anchor`` is a plain attribute write, so the flush that persists it would
        push ``updated_at`` to ``now()`` — landing the row ahead of PM's own clock by the
        POST round-trip. Since PM no-ops an identical re-observation *without advancing
        its clock*, that skew never resolves: the row is born into a permanent re-send
        loop (usa-wa#109 — the chronic org row sat exactly 228ms ahead of PM for 11 days
        on nothing else). Keeping the pre-stamp clock leaves the row *older* than PM, so
        the next reconcile takes the PM-wins branch, mirrors, and reaches parity.

        Every anchor-stamp site routes through here (CR-1): fixing only ``_deliver`` left
        the sweep's fallback stamp re-arming the same defect. A genuine local edit made
        before the stamp keeps its own clock and still wins LWW, as it should.

        A descriptor whose ``last_updated`` yields None for a row (the base default —
        i.e. it never overrode the pair) gets the anchor but no preserve; that is logged
        rather than silent, since LWW is already inoperable for such a descriptor.
        """
        preserved = descriptor.last_updated(row)
        descriptor.set_anchor(row, pm_id)
        if preserved is None:
            logger.debug(
                "anchor_stamp_clock_not_preserved",
                extra={"entity_type": descriptor.entity_type, "pm_id": str(pm_id)},
            )
            return
        descriptor.set_last_updated(row, preserved)

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
        if pm_ts is None:
            return
        # Skip the stamp at parity (CR-1). ``set_last_updated`` force-flags the column
        # dirty so the anchor-stamp preserve — a deliberate no-change write — survives the
        # flush; but this runs on the PM-wins/tie branch for every record of every
        # reconcile, so flagging unconditionally turned each already-converged row into a
        # no-op UPDATE writing an identical value (~12.7k/day across the anchored cohorts).
        #
        # Equality is a sufficient test here because ``upsert_from_pm`` flushes before
        # returning: a row PM actually changed has already had ``updated_at`` bumped to
        # ``now()`` by the ``onupdate``, so it no longer equals ``pm_ts`` and we stamp it.
        # Parity therefore means "converged and untouched", the only case worth skipping.
        if descriptor.last_updated(row) == pm_ts:
            return
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
            if now is None:
                # The cohort backstop self-heals dead anchors, which stamps retire/
                # heal timestamps — it needs a real clock, never a silent fallback (#36).
                raise ValueError("anchored_cohort reconcile requires an explicit now")
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
        now: datetime,
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
        # Resumable across restarts (#94): a persisted keyset checkpoint in the reconcile
        # stream's ``cursor`` lets an interrupted pass continue from where it stopped instead
        # of re-scanning the whole cohort from the top — which, at slow pacing on a large
        # cohort, never completed (so ``last_reconcile_at`` never stamped) and re-ran every
        # restart during a bulk produce. Only advanced with a ``commit`` hook (persisted per
        # page); ``None`` = start a fresh pass.
        state = await self._get_or_create_state(session, _reconcile_stream(descriptor))
        applied = 0
        last_id = as_ulid(state.cursor) if state.cursor else None
        while True:
            stmt = select(descriptor.model).where(anchor_col.is_not(None))
            if descriptor.deleted_column is not None:
                # Skip terminally-deleted rows — never re-fetch a tombstoned id. An
                # *archived* row (live anchor, deleted_at NULL) IS re-fetched, so a
                # dropped un-archive event is recovered here (#42).
                stmt = stmt.where(descriptor.deleted_column_expr().is_(None))
            if last_id is not None:
                # Resume trade-off (#94): rows at/below the checkpoint are skipped for the
                # rest of this pass, so a dropped feed event on a healthy-prefix row is not
                # re-fetched until the next *full* pass. If a row past the cursor permanently
                # raises (the #85 boundary rolls back its page but the prefix cursor stays
                # committed), every resume re-hits it and the prefix "freezes" — the poison
                # row is the actionable bug (surfaced by the #85 streak alert), not this skip.
                stmt = stmt.where(pk_col > last_id)
            stmt = stmt.order_by(pk_col).limit(self._sweep_batch_size)
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                break
            for row in rows:
                last_id = row.id
                pm_id = descriptor.anchor_value(row)
                record = await self._fetch_record_with_retry(descriptor, pm_id)
                if record is None:
                    # PM record gone (404): the entity was merged/deleted. Self-heal —
                    # re-anchor to the merge-winner, or retire on a genuine delete (#31).
                    await self._heal_dead_anchor(session, descriptor, row, now=now)
                    continue
                await self.apply_record(session, descriptor, record)
                # Re-evaluate enrichment for the anchored row (#34): a held identifier
                # (trigger gap) or a drifted carry payload (detection gap, check_drift)
                # re-enqueues an ENRICH here rather than waiting on a manual backfill.
                await self._maybe_enqueue_enrich(session, descriptor, record, row, check_drift=True)
                applied += 1
            if commit is not None:
                # Bound the open transaction to one page of PM round-trips (#13 CR) and
                # persist the keyset checkpoint with it, so a restart resumes here (#94).
                state.cursor = str(last_id)
                await session.flush()
                await commit()
            if len(rows) < self._sweep_batch_size:
                break
        # Full pass complete — clear the resume checkpoint so the next run starts fresh
        # (and the cadence gate, not the cursor, governs when that is, #94).
        state.cursor = None
        await session.flush()
        return applied

    async def _read_with_retry(
        self, make_awaitable: Callable[[], Awaitable[Any]], *, log_extra: dict
    ) -> Any:
        """Run a PM *read* with a bounded pause-and-resume on 429/5xx.

        usa-wa#85/#89: PM's rate limit is live, and a read burst is exactly what trips
        it. A :class:`RetryableClientError` sleeps the server's ``Retry-After`` hint
        (else the :data:`READ_BACKOFF_SECONDS` step) and retries in place, so the read
        resumes instead of aborting the cycle — which would leave the cadence unstamped,
        re-crawl from the top next cycle, and re-trip the limiter. Shared by every read
        whose bare 429 was cycle-fatal: the anchored-cohort reconcile (``read`` =
        ``reconcile``), the subscription backfill (``backfill``), and the changes-feed
        read (``feed``). A failure outlasting the budget re-raises into the caller's
        error boundary (the sidecar's per-component containment)."""
        for delay in READ_BACKOFF_SECONDS:
            try:
                return await make_awaitable()
            except RetryableClientError as exc:
                wait = exc.retry_after if exc.retry_after is not None else delay
                logger.warning(
                    "read_backoff",
                    extra={**log_extra, "wait_seconds": wait, "error": str(exc)},
                )
                await self._sleep(wait)
        return await make_awaitable()

    async def _fetch_record_with_retry(self, descriptor: EntityDescriptor, pm_id: Any) -> Any:
        """``descriptor.fetch_record`` with the shared read pause-and-resume (#85)."""
        return await self._read_with_retry(
            lambda: descriptor.fetch_record(self._client, pm_id),
            log_extra={
                "read": "reconcile",
                "entity_type": descriptor.entity_type,
                "pm_id": str(pm_id),
            },
        )

    async def fetch_record_with_retry(self, descriptor: EntityDescriptor, pm_id: Any) -> Any:
        """Public seam for the subscription backfill (usa-wa#89): fetch a newly-
        subscribed entity's current state with the same 429 pause-and-resume the
        reconcile crawl uses, so a rate-limit mid-backfill doesn't abort the backstop
        before it stamps (→ re-crawl → re-trip)."""
        return await self._read_with_retry(
            lambda: descriptor.fetch_record(self._client, pm_id),
            log_extra={
                "read": "backfill",
                "entity_type": descriptor.entity_type,
                "pm_id": str(pm_id),
            },
        )

    async def has_local_anchor(
        self, session: AsyncSession, descriptor: EntityDescriptor, pm_id: Any
    ) -> bool:
        """Whether a local row is already anchored to ``pm_id`` (usa-wa#89).

        The subscription backfill's skip gate: an entity we already hold locally is
        current via the feed + reconcile backstop and does not need a re-fetch."""
        return (await self._row_by_anchor(session, descriptor, pm_id)) is not None

    # --- read path: changes feed (incremental primary for person/org) --------

    async def process_feed(self, session: AsyncSession, *, now: datetime, limit: int = 100) -> int:
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
        # Read only the cursor value before the fetch (usa-wa#89): the SyncState row
        # acquisition (a potential INSERT + flush) is deferred to after the retried
        # get_changes, so a 429 pause-and-resume doesn't hold uncommitted state open
        # across the backoff sleeps (the feed runs inside the tick's transaction).
        after = _parse_after(await self._read_cursor(session, CHANGES_STREAM))
        page = await self._read_with_retry(
            lambda: self._client.get_changes(after, limit=limit),
            log_extra={"read": "feed", "after": after},
        )
        applied = 0
        for item in page.items:
            descriptor = self.descriptor_for(item.entity_type)
            if descriptor is None or descriptor.read_source == "none":
                continue
            if item.change_kind == "deleted":
                row = await self._row_by_anchor(session, descriptor, item.entity_id)
                if row is None or descriptor.is_deleted(row):
                    continue
                if item.merged_into is not None:
                    # Merge: PM names the surviving winner (power-map#235). Re-anchor any
                    # entity type to it deterministically — no identifier re-match.
                    await self._heal_dead_anchor(
                        session, descriptor, row, now=now, winner_hint=item.merged_into
                    )
                elif descriptor.supports_rematch:
                    # Bare delete on a rematch-capable descriptor (org): keep the #36
                    # backstop ahead of any retire. Identifier re-match re-anchors a merge
                    # whose event lacked merged_into — a PM gap, or a pre-power-map#235
                    # backlog delete — and retires only on a genuine miss. Same path as the
                    # 404 reconcile, so feed and backstop behave identically (CR #1).
                    await self._heal_dead_anchor(session, descriptor, row, now=now)
                elif descriptor.deleted_column is not None:
                    # Genuine delete for a non-rematch type (person/role/assignment): absent
                    # merged_into is unambiguous post-power-map#235, so delete — the
                    # merge/delete ambiguity that blocked this is gone (usa-wa#37). Distinct
                    # log key from the heuristic identifier-miss delete (CR #2).
                    descriptor.mark_deleted(row, now)
                    await session.flush()
                    logger.info(
                        "dead_anchor_deleted_via_feed",
                        extra={"entity_type": descriptor.entity_type, "local_id": str(row.id)},
                    )
                else:
                    # No tombstone column: defer to the heal routine's warn-and-leave.
                    await self._heal_dead_anchor(session, descriptor, row, now=now)
                continue
            record = await descriptor.fetch_record(self._client, item.entity_id)
            if record is None:
                continue
            await self.apply_record(session, descriptor, record)
            applied += 1
        # Persist the advanced cursor — acquiring (get-or-create) the state row only when
        # there is one to write (usa-wa#89 CR): an empty feed has nothing to persist, so
        # this skips both the row's get-or-create round-trip and the creation of an empty
        # state row on a first empty poll. _read_cursor above still resets a stale cursor
        # to 0 on every read, so a non-advancing feed is unaffected.
        if page.next_after is not None:
            state = await self._get_or_create_state(session, CHANGES_STREAM)
            state.cursor = str(page.next_after)
        await session.flush()
        return applied

    # --- sync-state helpers ---------------------------------------------------

    async def _read_cursor(self, session: AsyncSession, stream: str) -> str | None:
        """The stream's persisted cursor value, or None — a scalar read that does NOT
        materialise or create the SyncState row (usa-wa#89). Lets ``process_feed`` learn
        ``after`` before the retried fetch while deferring the row's get-or-create (a
        possible INSERT + flush) to the post-fetch cursor write."""
        return await session.scalar(select(SyncState.cursor).where(SyncState.stream == stream))

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


async def nonconverging_count(session: AsyncSession, *, threshold: int) -> int:
    """Rows currently at/over the non-convergence threshold (usa-wa#112).

    The standing set of rows PM keeps ``auto-attached``-matching without applying our
    diff — an identical payload re-sent every reconcile cycle. Free function like
    :func:`rejected_breakdown` so the sidecar's cycle summary reads it without an engine
    and alerts on a rise (the #85 pattern). A row converges (a real edit lands, or PM
    finally applies) → its counter resets to 0 → it drops out of this count.
    """
    return (
        await session.scalar(
            select(func.count())
            .select_from(NonConvergenceState)
            .where(NonConvergenceState.count >= threshold)
        )
    ) or 0


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
