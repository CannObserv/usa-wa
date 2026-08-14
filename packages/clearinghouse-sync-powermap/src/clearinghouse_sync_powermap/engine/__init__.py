"""SyncEngine — the daemon brain (write path + read path).

Stateless over a descriptor registry + a :class:`PowerMapClient`. Every method
takes an explicit ``session`` and (where a clock matters) an explicit ``now`` so
the logic is deterministic and unit-testable. The long-running daemon (step 7)
owns the loops and the wall clock; this class owns the per-cycle work.

Package layout (usa-wa#181 — AR-4)
==================================
``SyncEngine`` was one 2318-line class with 50 methods and the repo's #1 churn rate
(32 of 300 commits). It is now a **thin façade** over three managers, each in its own
module, with an immutable context beneath them. The façade's public surface is
unchanged, so all eight downstream call sites (including ``usa-wa-sync-powermap``) are
untouched and the split is revertible.

    context.py  EngineContext — the immutable floor: descriptor registry + drain
                priority, the PM client, every tunable, and the PM *read*
                pause-and-resume both paths need. No mutable engine state lives here.
    anchors.py  AnchorManager — the anchor stamp + LWW clock adopt, the
                one-row-per-anchor guard, by-anchor lookup, the re-anchor ledger
                (#108) and the non-convergence ledger (#112).
    write.py    OutboxWriter  — local → PM: the four enqueue triggers (sweep CREATE,
                the two ENRICHes, the LWW UPDATE), the drain, delivery, the terminal
                states (REJECTED / UNAVAILABLE), retry/backoff, and the redrive.
    read.py     Reconciler    — PM → local: ``apply_record`` (the single LWW arbiter),
                the reconcile backstops, the dead-anchor heal, the changes feed, the
                trailing replay, and the conditional-GET cache.

**The seam is the dependency direction, not the responsibility list.** The issue's
sketch proposed three peer modules; the call graph does not admit that. Read the four
as a strict DAG — ``context ← anchors ← write ← read ← façade`` — where each arrow is
forced by a real call:

  - ``anchors`` is *below* both paths, not a peer of them, because both stamp anchors:
    the drain does it on every delivery and the sweep does it on a PM-first adopt.
  - ``read → write`` because the read path is what *discovers* that a write is owed —
    ``apply_record``'s local-newer branch enqueues an UPDATE, and the anchored-cohort
    reconcile enqueues the drift ENRICH. Never the reverse.
  - The **dead-anchor heal** is filed under ``read``, not ``anchors`` as the issue
    proposed: both of its triggers are read-path events (a ``deleted`` feed item, a
    cohort re-fetch 404) and its body calls ``apply_record``. Filing it under anchors
    would add ``anchors → read`` on top of the unavoidable ``read → anchors`` — a cycle
    bought for nothing but a tidier-sounding module name.
  - The **PM read pause-and-resume** sits on the context rather than on ``read``,
    because the sweep (a write trigger) uses it too; leaving it on the Reconciler would
    make ``write`` and ``read`` mutually dependent.

**How they share state.** Constructor injection: the façade builds one
:class:`~clearinghouse_sync_powermap.engine.context.EngineContext` and hands it (plus the
managers each one genuinely needs) to each manager. The façade does **not** pass itself —
that would recreate the god object under a new name, since any manager could then reach any
other's methods. Nothing mutable is shared: the ``AsyncSession`` is still an explicit
per-call argument, and each per-process throttle/counter has exactly one owner
(``_warned_stuck`` + ``DrainStats`` → the writer, ``_warned_nonconverging`` → the anchor
manager, ``_warned_dead_anchors`` + the conditional-GET tallies → the reconciler). The
two anchor ledgers therefore return a ``bool`` instead of incrementing the drain's tally
object, and the writer counts its own drain.

Sync topology (the whole map, for readers + agents)
===================================================
Two directions, bidirectional sync between the local cache and PM. PM is the
system of record; the local cache is a query-latency mirror we *produce into*.

WRITE path (local → PM) — four triggers, one ledger (``OutboxEntry``), one drainer
(``OutboxWriter.drain_outbox``); see :mod:`clearinghouse_sync_powermap.engine.write` for
the trigger table and the UPDATE-vs-ENRICH keying rule.

READ path (PM → local) — the changes feed (primary), its trailing replay, and the
anchored-cohort reconcile (backstop), all converging on the single LWW arbiter
``Reconciler.apply_record``; see :mod:`clearinghouse_sync_powermap.engine.read` for the
dead-anchor self-heal contract.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_sync_powermap.client import PowerMapClient
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.engine.anchors import AnchorManager, nonconverging_count
from clearinghouse_sync_powermap.engine.context import (
    DEFAULT_DEFERRED_STUCK_THRESHOLD,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_NONCONVERGENCE_THRESHOLD,
    DEFAULT_REPLAY_MARGIN,
    DEFAULT_REPLAY_MAX_ITEMS,
    DEFAULT_REPLAY_RETAIN,
    DEFAULT_SWEEP_BATCH_SIZE,
    READ_BACKOFF_SECONDS,
    TRANSIENT_EXCEPTIONS,
    EngineContext,
    enrich_fingerprint,
)
from clearinghouse_sync_powermap.engine.read import (
    APPLY_INSERTED,
    APPLY_KEPT_LOCAL,
    APPLY_NOOP,
    APPLY_SKIPPED,
    APPLY_UPDATED,
    CHANGES_STREAM,
    MAX_RECONCILE_PAGES,
    REPLAY_STREAM,
    Reconciler,
    ReplayResult,
    _replay_floor,
)
from clearinghouse_sync_powermap.engine.write import (
    DrainStats,
    OutboxBacklog,
    OutboxWriter,
    outbox_backlog,
    rejected_breakdown,
)
from clearinghouse_sync_powermap.models import OutboxEntry

#: Re-exported so ``from clearinghouse_sync_powermap.engine import X`` keeps working for
#: every name the pre-#181 single module published — the façade's whole point.
#: ``_replay_floor`` is private but has a live consumer (``test_engine_replay.py`` pins
#: the floor arithmetic directly), so it is re-exported rather than orphaned.
__all__ = [
    "APPLY_INSERTED",
    "APPLY_KEPT_LOCAL",
    "APPLY_NOOP",
    "APPLY_SKIPPED",
    "APPLY_UPDATED",
    "CHANGES_STREAM",
    "DEFAULT_DEFERRED_STUCK_THRESHOLD",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_NONCONVERGENCE_THRESHOLD",
    "DEFAULT_REPLAY_MARGIN",
    "DEFAULT_REPLAY_MAX_ITEMS",
    "DEFAULT_REPLAY_RETAIN",
    "DEFAULT_SWEEP_BATCH_SIZE",
    "MAX_RECONCILE_PAGES",
    "READ_BACKOFF_SECONDS",
    "REPLAY_STREAM",
    "TRANSIENT_EXCEPTIONS",
    "AnchorManager",
    "DrainStats",
    "EngineContext",
    "OutboxBacklog",
    "OutboxWriter",
    "Reconciler",
    "ReplayResult",
    "SyncEngine",
    "_replay_floor",
    "enrich_fingerprint",
    "nonconverging_count",
    "outbox_backlog",
    "rejected_breakdown",
]


class SyncEngine:
    """Per-cycle sync work over a fixed descriptor registry.

    A thin façade (#181): every method here delegates to one of the three managers.
    Its job is to keep the pre-split call surface intact and to own the wiring — it
    holds no sync state of its own, so it cannot drift back into a god class. Reach a
    subsystem directly via :attr:`anchors` / :attr:`writer` / :attr:`reader` when a
    caller genuinely needs one; widen the façade only for something all three share.
    """

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
        replay_margin: int = DEFAULT_REPLAY_MARGIN,
        replay_retain: int = DEFAULT_REPLAY_RETAIN,
        replay_max_items: int = DEFAULT_REPLAY_MAX_ITEMS,
        conditional_get_enabled: bool = True,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._ctx = EngineContext(
            descriptors,
            client,
            batch_limit=batch_limit,
            max_attempts=max_attempts,
            deferred_stuck_threshold=deferred_stuck_threshold,
            sweep_batch_size=sweep_batch_size,
            nonconvergence_threshold=nonconvergence_threshold,
            replay_margin=replay_margin,
            replay_retain=replay_retain,
            replay_max_items=replay_max_items,
            conditional_get_enabled=conditional_get_enabled,
            sleep=sleep,
        )
        self._anchors = AnchorManager(self._ctx)
        self._writer = OutboxWriter(self._ctx, self._anchors)
        self._reader = Reconciler(self._ctx, self._anchors, self._writer)

    # --- subsystem access -----------------------------------------------------

    @property
    def anchors(self) -> AnchorManager:
        """The anchor + LWW-clock manager (#181)."""
        return self._anchors

    @property
    def writer(self) -> OutboxWriter:
        """The local → PM outbox write path (#181)."""
        return self._writer

    @property
    def reader(self) -> Reconciler:
        """The PM → local read path (#181)."""
        return self._reader

    # --- registry -------------------------------------------------------------

    def descriptor_for(self, entity_type: str) -> EntityDescriptor | None:
        return self._ctx.descriptor_for(entity_type)

    @property
    def descriptors(self) -> tuple[EntityDescriptor, ...]:
        """All registered descriptors (read-only). Lets membership managers (e.g. the
        subscription reconciler's local-cohort discovery) enumerate the entity set."""
        return self._ctx.descriptors

    # --- write path -----------------------------------------------------------

    @property
    def last_drain_stats(self) -> DrainStats:
        """Disposition + re-anchor tallies from the most recent :meth:`drain_outbox`."""
        return self._writer.last_drain_stats

    async def sweep_unanchored(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        *,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> int:
        """Enqueue a CREATE for every locally-minted row with a null anchor
        (``OutboxWriter.sweep_unanchored``)."""
        return await self._writer.sweep_unanchored(session, descriptor, commit=commit)

    async def drain_outbox(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        commit: Callable[[], Awaitable[None]] | None = None,
        chunk_size: int = 1,
    ) -> list[OutboxEntry]:
        """Process all due PENDING entries once (``OutboxWriter.drain_outbox``)."""
        return await self._writer.drain_outbox(
            session, now=now, commit=commit, chunk_size=chunk_size
        )

    async def count_unavailable(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        entity_type: str | None = None,
        older_than: timedelta | None = None,
    ) -> int:
        """Count re-drivable (``UNAVAILABLE``) entries matching the scope, non-mutating."""
        return await self._writer.count_unavailable(
            session, now=now, entity_type=entity_type, older_than=older_than
        )

    async def redrive_unavailable(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        entity_type: str | None = None,
        older_than: timedelta | None = None,
        limit: int | None = None,
    ) -> int:
        """Reset dead-lettered (``UNAVAILABLE``) entries back to ``PENDING``, due now."""
        return await self._writer.redrive_unavailable(
            session, now=now, entity_type=entity_type, older_than=older_than, limit=limit
        )

    def _log_deferral(self, entry: OutboxEntry, now: datetime) -> None:
        """Retained private delegate (#181): the #15 deferred-stuck throttle is specified
        by a unit test that drives this directly, and the split must not force a test edit
        to prove it changed nothing. New callers should use ``engine.writer`` instead."""
        self._writer._log_deferral(entry, now)

    # --- read path ------------------------------------------------------------

    @property
    def conditional_get_stats(self) -> tuple[int, int]:
        """``(skipped, fetched)`` conditional-GET tallies accumulated since the last reset
        (usa-wa#160): rows the reconcile skipped on a ``304`` vs. re-fetched full."""
        return self._reader.conditional_get_stats

    def reset_conditional_get_stats(self) -> None:
        """Zero the conditional-GET tallies (the sidecar calls this at each cycle start)."""
        self._reader.reset_conditional_get_stats()

    async def apply_record(
        self, session: AsyncSession, descriptor: EntityDescriptor, record: dict
    ) -> str:
        """Upsert one PM record into the local cache under last-write-wins
        (``Reconciler.apply_record`` — the single LWW arbiter)."""
        return await self._reader.apply_record(session, descriptor, record)

    async def reconcile(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        *,
        now: datetime | None = None,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> int:
        """Run the descriptor's reconcile backstop, dispatched by ``reconcile_mode``."""
        return await self._reader.reconcile(session, descriptor, now=now, commit=commit)

    async def process_feed(self, session: AsyncSession, *, now: datetime, limit: int = 100) -> int:
        """Pull one batch of changes, apply them, and advance the cursor."""
        return await self._reader.process_feed(session, now=now, limit=limit)

    async def replay_from_floor(
        self, session: AsyncSession, *, now: datetime, limit: int = 100
    ) -> ReplayResult:
        """Re-read a trailing window of the changes feed and re-apply each item (#159)."""
        return await self._reader.replay_from_floor(session, now=now, limit=limit)

    async def fetch_record_with_retry(self, descriptor: EntityDescriptor, pm_id: Any) -> Any:
        """Public seam for the subscription backfill (usa-wa#89): fetch a newly-
        subscribed entity's current state with the same 429 pause-and-resume the
        reconcile crawl uses, so a rate-limit mid-backfill doesn't abort the backstop
        before it stamps (→ re-crawl → re-trip)."""
        return await self._ctx.fetch_record_for_backfill(descriptor, pm_id)

    async def has_local_anchor(
        self, session: AsyncSession, descriptor: EntityDescriptor, pm_id: Any
    ) -> bool:
        """Whether a local row is already anchored to ``pm_id`` (usa-wa#89).

        The subscription backfill's skip gate: an entity we already hold locally is
        current via the feed + reconcile backstop and does not need a re-fetch."""
        return await self._reader.has_local_anchor(session, descriptor, pm_id)
