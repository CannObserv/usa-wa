"""Reconciler — the PM → local read path (#181).

Extracted from ``SyncEngine`` unchanged. Owns the LWW arbiter every read converges on,
the two reconcile backstops, the changes feed and its trailing replay, the
conditional-GET cache, and the ``SyncState`` cursors those all persist.

    context  ←  anchors  ←  write  ←  **read**  ←  __init__ (the SyncEngine façade)

Top of the DAG: it consumes :class:`~clearinghouse_sync_powermap.engine.anchors.AnchorManager`
(stamp the anchor, adopt PM's clock, look a row up by anchor) and
:class:`~clearinghouse_sync_powermap.engine.write.OutboxWriter` (the LWW local-newer branch
and the anchored-cohort re-enrich are *enqueue* triggers), and nothing consumes it but the
façade. That direction is forced: the read path decides when a write is owed, the write
path never decides when to read.

**Why the dead-anchor heal lives here and not in ``anchors``.** The issue's sketch filed it
under anchors, but all of its triggers are read-path events — a ``deleted`` changes-feed
item (:meth:`Reconciler.process_feed`), a 404 on a feed/replay item's detail fetch
(:meth:`Reconciler._apply_feed_page`, usa-wa#213), and a 404 on the cohort re-fetch
(:meth:`Reconciler._reconcile_anchored_cohort`) — and its body calls
:meth:`Reconciler.apply_record` to adopt the winner's canonical fields. Filing it under
anchors would put ``anchors → read`` alongside the unavoidable ``read → anchors``, i.e. a
cycle, and would do so to honour a grouping the call graph does not support. The *anchor
primitives* it uses (stamp, by-anchor lookup) are in ``anchors``; the *policy* that decides
a dead anchor's fate is a read-path concern and lives here.

READ paths (PM → local) — both converge on the single LWW arbiter
:meth:`Reconciler.apply_record` (PM-newer-or-tie → PM wins; local-newer → keep + maybe
enqueue an UPDATE), so the clock comparison lives in exactly one place:

  - :meth:`Reconciler.process_feed`   — incremental PRIMARY: PM's changes feed.
  - :meth:`Reconciler.replay_from_floor` — the trailing re-read of that feed (usa-wa#159),
                                       covering PM's at-least-once concurrent-commit skip.
  - :meth:`Reconciler.reconcile` →
    :meth:`Reconciler._reconcile_anchored_cohort` — bounded BACKSTOP: re-fetch only OUR
                                       anchored rows to recover a dropped feed event
                                       (and, since #34, to re-enrich).

  Enrich re-evaluation lives ONLY on the reconcile backstop, not the feed: the reconcile is
  the one path that already walks the whole anchored cohort, so a held-identifier change or
  carry drift self-heals on the reconcile cadence (hourly), not per feed event.
  Consolidating all three enrich triggers into ``apply_record`` is a tracked simplification
  (usa-wa#35).

DEAD-ANCHOR self-heal (usa-wa#31/#36/#37) — a PM-side merge deletes the loser and keeps the
winner, orphaning our anchor. Every read path detects it and routes to
:meth:`Reconciler._heal_dead_anchor`: ``process_feed`` on a ``deleted`` event (the timely
signal), the shared feed/replay apply on a detail-fetch 404 (usa-wa#213), and
``_reconcile_anchored_cohort`` on a re-fetch 404 (the backstop). The winner is
resolved from one of two signals, in order of trust:

  - PM's explicit ``merged_into`` on the ``deleted`` event (power-map#235, consumed in
    usa-wa#37) — deterministic, so it re-anchors *any* entity type generically with no
    identifier re-match.
  - identifier re-match — the backstop when no ``merged_into`` was seen (a 404, or a bare
    ``deleted`` for a rematch-capable org). Only the org descriptor supports it.

A bare ``deleted`` (no ``merged_into``) is otherwise an unambiguous genuine delete
post-power-map#235: non-rematch types (person/role/assignment) delete (``deleted_at``). The
heal also retires a duplicate orphan when a many-to-one merge already left another local row
on the winner, and a non-rematch type with no winner signal at all (a 404 backstop) logs once
and leaves the row. Retired rows are excluded from the sweep and reconcile.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.client import ChangePage
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid
from clearinghouse_sync_powermap.engine.anchors import AnchorManager
from clearinghouse_sync_powermap.engine.context import EngineContext
from clearinghouse_sync_powermap.engine.write import OutboxWriter
from clearinghouse_sync_powermap.models import (
    OP_UPDATE,
    ConditionalGetState,
    SyncState,
)

logger = get_logger(__name__)

#: SyncState stream key for the shared PM changes feed.
CHANGES_STREAM = "changes_feed"

#: SyncState stream key for the trailing changes-feed *replay* backstop (usa-wa#159).
#: Distinct from ``CHANGES_STREAM``: the live feed advances its cursor to the newest
#: seq consumed, while replay re-reads a trailing window each cycle to re-cover PM's
#: at-least-once concurrent-commit skip (power-map#387). Its ``last_reconcile_at``
#: gates the replay cadence; its ``cursor`` is the **verified watermark** (usa-wa#211,
#: load-bearing since then): the seq the last pass caught up to, read back at the start
#: of every pass to derive the floor (``verified − retain``) — this is what lets the
#: window narrow as it converges instead of re-reading a flat ``high_water − margin``
#: trail forever (the #211 rate-limit saturation). Only written at pass end, so a crash
#: mid-pass re-reads from the previous watermark next cycle — at-least-once, never a
#: gap. Persisting it also re-costs a restart: the first post-restart pass resumes from
#: the watermark instead of re-crawling the full margin.
REPLAY_STREAM = "changes_replay"

#: Safety ceiling on the legacy ``full_list`` reconcile pagination loop (#6). The
#: full-list backstop is dead for usa-wa post-#10 (cohort producers use the bounded
#: ``anchored_cohort`` backstop, jurisdictions use ``none``) but live for siblings, so
#: a misbehaving PM that always advertises another cursor — or a non-advancing one —
#: would otherwise spin the daemon forever. Mirrors the live ``discover`` /
#: ``list_subscriptions`` page guard in ``pmclient`` (warn + break with the partial
#: set). At a typical 100 records/page this is ~100k records — orders of magnitude
#: above any bounded PM list — so it never trips normally; a runaway guard, not a knob.
MAX_RECONCILE_PAGES = 1000

#: Outcomes of applying one PM record under LWW (returned for observability/tests).
APPLY_INSERTED = "inserted"
APPLY_UPDATED = "updated"
#: The PM-wins branch re-upserted identical values — no column changed (usa-wa#212).
#: This is the converged steady state at LWW clock parity: ``adopt_remote_clock``
#: mirrors PM's clock onto the row on every apply, so re-reading an already-applied
#: record (each replay pass re-reads its whole trailing window) lands here. Excluded
#: from the replay ``healed`` delta, so a converged system legitimately reports zero
#: heals — previously every such re-apply was misreported as ``updated`` and
#: ``replay_healed`` was structurally incapable of reaching zero.
APPLY_NOOP = "noop"
APPLY_KEPT_LOCAL = "kept_local"
#: An update-only descriptor (org/person/role/assignment) declined to mirror a PM
#: record it has never produced (``upsert_from_pm`` returned None) — not an insert.
APPLY_SKIPPED = "skipped"


@dataclass
class ReplayResult:
    """Outcome of one :meth:`Reconciler.replay_from_floor` pass (usa-wa#159).

    ``applied`` is how many feed items the trailing re-read processed through the LWW
    upsert path. **Since the #160 residual it excludes items short-circuited on a 304** —
    i.e. exactly the already-current rows that used to dominate it as idempotent no-ops —
    so the number steps down sharply the first cycle conditional GET is enabled, and it is
    no longer a proxy for the window's size. The skipped count is the ``skipped`` half of
    :meth:`Reconciler.conditional_get_stats` on the same cycle-summary line; ``applied +
    skipped`` is the old quantity. ``healed`` is the subset that actually changed a local
    row (``inserted``/``updated``) — the **would-heal delta** the Phase-A shadow rollout
    measures: a persistently non-zero ``healed`` is proof replay is recovering events the
    live feed dropped/skipped (its reason to exist). Since usa-wa#212 an identical-value
    re-apply reports ``noop`` and is excluded, so ``healed`` legitimately reaches zero on
    a converged system — before that, every already-converged item in the window was
    misreported as ``updated`` and the signal could never reach zero. **``healed`` is
    unaffected by the 304 short-circuit** (a 304 means nothing to heal), so the Phase-B
    go/no-go gate reads the same before and after — which is why the ``applied``
    discontinuity is acceptable.
    ``fell_off`` is True when the replay floor sat below PM's oldest-retained
    watermark (``meta.min_seq``, power-map#388): the ``[floor, min_seq)`` slice was
    pruned from the 90-day window, so replay could not cover it and the caller must fall
    back to a full cohort scan for that gap. ``floor``/``high_water`` are surfaced for
    the cycle-summary log.

    ``items`` (usa-wa#211) is the pass's total feed-item enumeration — the
    request-budget observable: each item costs at most one detail GET (a ``304`` or
    ``404`` still spends a rate-limit token), so this bounds the pass's PM detail
    traffic where ``applied`` (which excludes 304-skips and delete-routed items) does
    not. ``budget_exhausted`` is True when the pass stopped at ``replay_max_items``
    and carried the remainder over via the verified watermark.
    """

    applied: int
    healed: int
    fell_off: bool
    floor: int
    high_water: int
    items: int = 0
    budget_exhausted: bool = False


class Reconciler:
    """The read path: LWW apply, reconcile backstops, changes feed, replay, ETag cache."""

    def __init__(self, ctx: EngineContext, anchors: AnchorManager, writer: OutboxWriter) -> None:
        self._ctx = ctx
        self._anchors = anchors
        self._writer = writer
        #: Local row ids already surfaced as an unhealable dead anchor this process, so
        #: a descriptor that can't re-match (person/role/assignment until power-map#235)
        #: warns once per wedged row rather than every reconcile cycle (#36). Same
        #: throttle shape as the writer's ``_warned_stuck``; a restart re-warns once.
        self._warned_dead_anchors: set = set()
        #: Cumulative conditional-GET tallies across this cycle's per-descriptor reconciles
        #: **and the replay backstop** (usa-wa#160): reads skipped on a 304 vs. fetched full
        #: on a 200/first pass. Accumulated here (each runs in its own session but one
        #: shared engine); the sidecar reads them for the cycle summary and resets per cycle.
        self._conditional_get_skipped = 0
        self._conditional_get_fetched = 0

    @property
    def conditional_get_stats(self) -> tuple[int, int]:
        """``(skipped, fetched)`` conditional-GET tallies accumulated since the last reset
        (usa-wa#160): reads the anchored-cohort reconcile and the replay backstop skipped on
        a ``304`` vs. re-fetched full. The live changes feed never contributes — it stays
        unconditional by design (see :meth:`_apply_feed_page`)."""
        return (self._conditional_get_skipped, self._conditional_get_fetched)

    def reset_conditional_get_stats(self) -> None:
        """Zero the conditional-GET tallies (the sidecar calls this at each cycle start)."""
        self._conditional_get_skipped = 0
        self._conditional_get_fetched = 0

    # --- the LWW arbiter ------------------------------------------------------

    async def apply_record(
        self, session: AsyncSession, descriptor: EntityDescriptor, record: dict
    ) -> str:
        """Upsert one PM record into the local cache under last-write-wins.

        - No local row → insert (or, for update-only descriptors that decline to
          mirror an unproduced record, skip).
        - Local row strictly newer than the PM record → keep local; enqueue an
          UPDATE to push it up (only when the entity is write-enabled).
        - Otherwise (PM newer, or tie) → PM wins; overwrite the local row. Reports
          ``APPLY_UPDATED`` only when a column actually changed; a re-upsert of
          identical values (the converged steady state at clock parity) reports
          ``APPLY_NOOP`` (usa-wa#212), so consumers counting repairs can converge
          to zero.
        """
        existing = await descriptor.local_match(session, record)
        if existing is None:
            row = await descriptor.upsert_from_pm(session, record)
            if row is None:
                # Update-only descriptor declined to mirror an unproduced record.
                return APPLY_SKIPPED
            self._anchors.adopt_remote_clock(descriptor, row, record)
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
                    self._anchors.stamp_anchor(descriptor, existing, pm_id)
            if descriptor.write_enabled:
                if await descriptor.local_newer_is_noop(session, existing, record):
                    # #102: local is "newer" only by a spurious clock skew — re-producing this
                    # row would not change PM. Adopt PM's clock (parity) instead of enqueuing an
                    # identical observation the reconcile would re-send every cycle forever.
                    self._anchors.adopt_remote_clock(descriptor, existing, record)
                elif not await self._writer.rejected_identical_update(
                    session, descriptor, existing
                ):
                    # #132: skip only the provably-futile replay of a payload PM just
                    # 422-refused. Deliberately NOT a clock adopt — the pending change
                    # is real and must re-send the moment the local data is fixed.
                    await self._writer.enqueue(session, descriptor, existing, OP_UPDATE)
            return APPLY_KEPT_LOCAL

        row = await descriptor.upsert_from_pm(session, record, existing=existing)
        if row is None:
            # Defensive: an update-only descriptor declining even with ``existing``
            # passed — nothing was written, so don't report an update.
            return APPLY_SKIPPED
        # Honest outcome (usa-wa#212): did the upsert actually change a column?
        # ``upsert_from_pm`` flushes before returning, so a net column change lets the
        # ``onupdate`` bump the row's clock to ``now()`` — the same signal
        # ``adopt_remote_clock``'s parity guard relies on (see its docstring). At LWW
        # parity (the converged steady state: the clock was mirrored on the prior
        # apply) an identical re-upsert registers no net change, the flush emits no
        # UPDATE, and the clock still equals PM's → APPLY_NOOP. A strictly-newer PM
        # record reports APPLY_UPDATED even when no mirrored column changed (a PM
        # touch-only bump): the clock genuinely advanced and is adopted below.
        # This detection requires the shared base's *Python-side* ``onupdate``
        # (clearinghouse_core.models) — a server-side onupdate would leave the
        # attribute unrefreshed after flush and break the comparison.
        changed = descriptor.last_updated(row) != lu_pm
        self._anchors.adopt_remote_clock(descriptor, row, record)
        return APPLY_UPDATED if changed else APPLY_NOOP

    # --- merge-orphan self-heal (usa-wa#31 / power-map#235) -------------------

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
            winner = await descriptor.rematch_anchor(self._ctx.client, session, row)
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
        holder = await self._anchors.row_by_anchor(session, descriptor, winner)
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
        self._anchors.stamp_anchor(descriptor, row, winner)
        # fetch_record can 404 here if the named winner was itself later merged away (a
        # merge chain). We've re-anchored to it regardless, so the row is briefly a fresh
        # dead anchor — harmless: the next feed deleted(winner) / reconcile 404 re-heals
        # it to the final winner. We only adopt canonical fields + re-enrich when the
        # winner resolves (CR #7).
        record = await descriptor.fetch_record(self._ctx.client, winner)
        if record is not None:
            await self.apply_record(session, descriptor, record)
            await self._writer.maybe_enqueue_enrich(
                session, descriptor, record, row, check_drift=True
            )
        await session.flush()
        logger.info(
            "dead_anchor_reanchored",
            extra={**log_ctx, "old_anchor": str(old), "winner": str(winner)},
        )

    # --- reconcile backstops --------------------------------------------------

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

        Transaction boundary (#13 CR): like ``OutboxWriter.drain_outbox``, each page makes
        PM network round-trips. When a ``commit`` callback is supplied the backstop
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
            page = await self._ctx.client.list_entities(descriptor.read_path, params)
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

        The return value counts every row re-fetched and run through the arbiter —
        including ``noop`` verifications of already-converged rows (usa-wa#212) — a
        coverage/throughput count, deliberately not a heal count (the sidecar discards
        it; tests pin the walk's extent with it).

        Keyset-paged by primary key (``sweep_batch_size`` at a time), mirroring
        ``OutboxWriter.sweep_unanchored``, so a large anchored cohort never materialises
        all at once. Unlike the sweep, the anchor is *not* mutated here, so the
        ``anchor IS NOT NULL`` set is stable across pages — but keyset paging by PK
        still gives a deterministic, terminating walk.

        Re-enrich (#34) is evaluated here, not on the changes-feed apply path: this is
        the single place that re-derives the carry payload for the whole anchored
        cohort, so a held-identifier change or carry-field drift self-heals on the
        reconcile cadence (hourly) rather than per feed event. A feed bump alone does
        not re-enrich — it defers to this backstop (see
        ``OutboxWriter.maybe_enqueue_enrich``).
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
            stmt = stmt.order_by(pk_col).limit(self._ctx.sweep_batch_size)
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                break
            for row in rows:
                last_id = row.id
                pm_id = descriptor.anchor_value(row)
                # Conditional GET (usa-wa#160 / power-map#385): send the stored ETag and
                # skip the whole row on a 304 — no body, no apply_record, no PM→local mirror
                # (the local carry-drift enrich still fires, see below). PM's detail ETag
                # covers child tables (incl. events), so a 304 means nothing to heal from PM.
                # A stale/absent ETag only ever costs a 200 we re-apply (idempotent), never
                # a missed update. Disabled → the unconditional fetch, unchanged.
                if self._ctx.conditional_get_enabled:
                    stored = await self._load_detail_etag(session, descriptor.entity_type, row.id)
                    fetch = await self._ctx.fetch_record_conditional_with_retry(
                        descriptor, pm_id, stored
                    )
                    if fetch.not_modified:
                        self._conditional_get_skipped += 1
                        # A 304 skips the PM→local apply, but NOT the local→PM carry-payload
                        # drift backstop (#160 CR): a newly-added carry field reaching the
                        # cohort is a local change PM hasn't seen, so PM 304s every row — the
                        # drift enrich must still fire or the field never propagates.
                        await self._writer.maybe_enqueue_enrich_drift_only(session, descriptor, row)
                        continue
                    record = fetch.record
                    new_etag = fetch.etag
                else:
                    record = await self._ctx.fetch_record_with_retry(descriptor, pm_id)
                    new_etag = None
                if record is None:
                    # PM record gone (404): the entity was merged/deleted. Self-heal —
                    # re-anchor to the merge-winner, or retire on a genuine delete (#31).
                    await self._heal_dead_anchor(session, descriptor, row, now=now)
                    continue
                await self.apply_record(session, descriptor, record)
                # Re-evaluate enrichment for the anchored row (#34): a held identifier
                # (trigger gap) or a drifted carry payload (detection gap, check_drift)
                # re-enqueues an ENRICH here rather than waiting on a manual backfill.
                await self._writer.maybe_enqueue_enrich(
                    session, descriptor, record, row, check_drift=True
                )
                if self._ctx.conditional_get_enabled:
                    if new_etag is not None:
                        # Store PM's fresh validator so next pass can 304 this row.
                        await self._store_detail_etag(
                            session, descriptor.entity_type, row.id, new_etag
                        )
                    # Count a genuine full re-fetch (enabled path only — disabled leaves the
                    # tally at 0 so the summary doesn't read as conditional GET having run).
                    self._conditional_get_fetched += 1
                applied += 1
            if commit is not None:
                # Bound the open transaction to one page of PM round-trips (#13 CR) and
                # persist the keyset checkpoint with it, so a restart resumes here (#94).
                state.cursor = str(last_id)
                await session.flush()
                await commit()
            if len(rows) < self._ctx.sweep_batch_size:
                break
        # Full pass complete — clear the resume checkpoint so the next run starts fresh
        # (and the cadence gate, not the cursor, governs when that is, #94).
        state.cursor = None
        await session.flush()
        return applied

    # --- conditional-GET cache (usa-wa#160) -----------------------------------

    async def _load_detail_etag(
        self, session: AsyncSession, entity_type: str, local_id: Any
    ) -> str | None:
        """The stored PM detail ETag for one anchored row, or None (usa-wa#160)."""
        return await session.scalar(
            select(ConditionalGetState.detail_etag).where(
                ConditionalGetState.entity_type == entity_type,
                ConditionalGetState.local_id == local_id,
            )
        )

    async def _store_detail_etag(
        self, session: AsyncSession, entity_type: str, local_id: Any, etag: str
    ) -> None:
        """Upsert the PM detail ETag for one anchored row (usa-wa#160)."""
        state = (
            await session.execute(
                select(ConditionalGetState).where(
                    ConditionalGetState.entity_type == entity_type,
                    ConditionalGetState.local_id == local_id,
                )
            )
        ).scalar_one_or_none()
        if state is None:
            state = ConditionalGetState(entity_type=entity_type, local_id=local_id)
            session.add(state)
        state.detail_etag = etag
        await session.flush()

    async def _anchored_row_etag(
        self, session: AsyncSession, descriptor: EntityDescriptor, pm_id: Any
    ) -> str | None:
        """The stored detail ETag for a feed item's PM id, or ``None`` (usa-wa#160).

        The replay path's entry into the store: the cohort reconcile already holds the row
        it is walking, but a feed/replay item names only a PM id, and the store is keyed on
        the local one. ``None`` when nothing anchors to ``pm_id`` — a first sighting reads
        unconditionally and stores its validator after the insert.

        Only the validator is returned, not the row: the caller must re-resolve the anchor
        *after* the apply anyway (see :meth:`_store_feed_etag`), so handing back a row that
        must not be trusted post-apply would be an invitation to misuse it.
        """
        row = await self._anchors.row_by_anchor(session, descriptor, pm_id)
        if row is None:
            return None
        return await self._load_detail_etag(session, descriptor.entity_type, row.id)

    async def _store_feed_etag(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        pm_id: Any,
        etag: str | None,
    ) -> None:
        """Persist a replayed item's fresh ETag so the next trailing pass can 304 it (#160).

        The anchor is re-resolved **after** the apply, deliberately (#160 CR): the store is
        keyed on the local id, and the apply can land the record on a different local row
        than the pre-fetch lookup returned — ``local_match`` keys on the natural key, which
        need not be the row currently holding the anchor. Keying off the stale pre-fetch row
        would strand the validator on a superseded row (harmless — the next pass reads
        unconditionally — but it leaves a dead ``ConditionalGetState`` behind and forfeits
        the 304). Re-resolving is the same single query either way, since a first sighting
        inside the replay window has no pre-fetch row to reuse.

        No ETag (PM omitted the header) or no row (an update-only descriptor declined the
        record) stores nothing.
        """
        if etag is None:
            return
        row = await self._anchors.row_by_anchor(session, descriptor, pm_id)
        if row is None:
            return
        await self._store_detail_etag(session, descriptor.entity_type, row.id, etag)

    async def has_local_anchor(
        self, session: AsyncSession, descriptor: EntityDescriptor, pm_id: Any
    ) -> bool:
        """Whether a local row is already anchored to ``pm_id`` (usa-wa#89).

        The subscription backfill's skip gate: an entity we already hold locally is
        current via the feed + reconcile backstop and does not need a re-fetch."""
        return (await self._anchors.row_by_anchor(session, descriptor, pm_id)) is not None

    # --- changes feed (incremental primary for person/org) --------------------

    async def process_feed(self, session: AsyncSession, *, now: datetime, limit: int = 100) -> int:
        """Pull one batch of changes, apply them, and advance the cursor.

        The feed yields ``(entity_type, id, change_kind)`` only, so each change
        is resolved to a full record via :meth:`PowerMapClient.get_entity`
        before upsert. A ``deleted`` event is the timely merge-orphan signal: if it
        names a row we anchored, route it to :meth:`_heal_dead_anchor` (re-anchor to
        the merge-winner, or retire on a genuine delete, #31); a delete for an entity
        we never produced is still skipped. A 404 on the detail fetch of an anchored
        row routes to the same heal (usa-wa#213 — previously a silent skip that left
        the dead anchor unhealed forever). ``now`` stamps any retirement (threaded
        from the sidecar tick; falls back to wall clock for ad-hoc callers).

        Read-path scope note: a permanent client error here (the typed
        :class:`DeliveryBlockedError` / :class:`PayloadRejectedError`, e.g. a
        mis-scoped read key) is intentionally *not* caught — there is no per-entry
        "park" for reads, and a read PM can't make forward progress at all if its
        credential is rejected, so the error propagates and the per-cycle isolation
        rolls back + logs the cycle. Only the write path (``OutboxWriter._deliver``)
        parks permanent failures, because there a single poison entry must not starve
        the rest of the outbox.
        """
        # Read only the cursor value before the fetch (usa-wa#89): the SyncState row
        # acquisition (a potential INSERT + flush) is deferred to after the retried
        # get_changes, so a 429 pause-and-resume doesn't hold uncommitted state open
        # across the backoff sleeps (the feed runs inside the tick's transaction).
        after = _parse_after(await self._read_cursor(session, CHANGES_STREAM))
        page = await self._ctx.read_with_retry(
            lambda: self._ctx.client.get_changes(after, limit=limit),
            log_extra={"read": "feed", "after": after},
        )
        applied, _ = await self._apply_feed_page(session, page, now=now)
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

    async def _apply_feed_page(
        self,
        session: AsyncSession,
        page: ChangePage,
        *,
        now: datetime,
        conditional: bool = False,
        dead_ids: set[ULID] | None = None,
    ) -> tuple[int, int]:
        """Apply every item in one changes-feed page; return ``(processed, healed)``.

        The shared body of the live feed (:meth:`process_feed`) and the trailing replay
        backstop (:meth:`replay_from_floor`, usa-wa#159), factored out so the two paths
        can never diverge — a replayed event heals exactly the way a live one would
        (merge/delete routing *and* the detail-fetch-404 routing included, usa-wa#213),
        and re-applying an already-current item is an idempotent LWW no-op. Does NOT
        touch any feed cursor; the caller owns cursor advancement (the live feed
        advances ``changes_feed``, replay stamps ``changes_replay``).

        ``dead_ids`` (usa-wa#213) is the per-pass memory of PM ids already found dead —
        via a ``deleted`` event or a detail-fetch 404 — so a window holding many stale
        items for the same gone entity costs one fetch, not one per item (one dead org
        accounted for ~20 fetches per replay pass). The replay caller threads one set
        across its whole multi-page pass; ``None`` scopes the throttle to this page.

        ``processed`` counts items run through the LWW arbiter (the historical
        ``process_feed`` return); ``healed`` is the subset whose LWW outcome actually
        changed a local row (``inserted``/``updated``) — the replay would-heal delta. A
        ``noop`` re-apply of identical values counts toward ``processed`` but never
        ``healed`` (usa-wa#212). Delete-routed items count toward neither (the dominant
        replay-recovered case is a stale upsert).

        ``conditional`` (usa-wa#160) sends the row's stored PM ETag as ``If-None-Match``
        and skips the item on a ``304``. **Only the replay caller passes it**, and that
        asymmetry is the point: a live feed item *means* PM changed the entity, so its
        stored validator is stale by construction — the conditional read would be a
        guaranteed ``200`` bought with two extra local queries (the anchor lookup + the
        ETag load) per item. Replay re-reads a trailing window of items it has *already*
        applied, which is exactly where the validator still matches. Gated by the shared
        ``conditional_get_enabled`` kill switch; a 304 skips only the PM→local apply (the
        feed path has no enrich to strand — re-enrich lives on the reconcile backstop
        alone, see the module docstring).
        """
        processed = 0
        healed = 0
        if dead_ids is None:
            dead_ids = set()
        for item in page.items:
            descriptor = self._ctx.descriptor_for(item.entity_type)
            if descriptor is None or descriptor.read_source == "none":
                continue
            if item.change_kind == "deleted":
                # The id is dead in PM either way — remember it so a later upsert item
                # for it in this pass doesn't buy a guaranteed 404 (#213).
                dead_ids.add(item.entity_id)
                row = await self._anchors.row_by_anchor(session, descriptor, item.entity_id)
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
            if item.entity_id in dead_ids:
                # Already found dead earlier in this pass (a deleted event or a 404) —
                # skip the re-fetch; the heal already ran once for this id (#213).
                continue
            use_conditional = conditional and self._ctx.conditional_get_enabled
            new_etag = None
            if use_conditional:
                # The ETag store is keyed on the LOCAL id, so the anchor has to be resolved
                # before the read. No anchor (an entity we hold no row for yet) simply reads
                # unconditionally — same request the plain path would make.
                stored = await self._anchored_row_etag(session, descriptor, item.entity_id)
                fetch = await descriptor.fetch_record_conditional(
                    self._ctx.client, item.entity_id, if_none_match=stored
                )
                if fetch.not_modified:
                    # PM's detail ETag covers child tables (incl. events) via the
                    # touch-cascade, so a 304 means PM holds nothing we have not already
                    # applied — there is no PM→local delta to mirror.
                    #
                    # What the skip *does* forgo (#160 CR): `apply_record`'s local-newer
                    # branch, which re-enqueues an outbox UPDATE when OUR row is ahead of
                    # PM's. So this is not "the re-apply was a no-op" in every case — for a
                    # row edited locally between two replay passes it was a write-back
                    # re-trigger. Acceptable because that re-trigger is a backstop, not the
                    # path: producers enqueue the UPDATE at write time, and the anchored
                    # reconcile's own 304 fast-path already skips the same branch. A 304
                    # strands no *enrich* signal either — unlike the reconcile, the feed
                    # path never ran `maybe_enqueue_enrich` to begin with.
                    self._conditional_get_skipped += 1
                    continue
                record, new_etag = fetch.record, fetch.etag
            else:
                record = await descriptor.fetch_record(self._ctx.client, item.entity_id)
            if record is None:
                # PM record gone (404): a dead anchor surfaced by a live/replayed upsert
                # item. Route it through the same heal the reconcile backstop's re-fetch
                # 404 uses — re-anchor via merged-into/identifier re-match, retire, or
                # warn-once — so feed and backstop behave identically (usa-wa#213; the
                # silent skip here left dead anchors re-fetched forever, never healed).
                dead_ids.add(item.entity_id)
                row = await self._anchors.row_by_anchor(session, descriptor, item.entity_id)
                if row is not None and not descriptor.is_deleted(row):
                    await self._heal_dead_anchor(session, descriptor, row, now=now)
                continue
            outcome = await self.apply_record(session, descriptor, record)
            processed += 1
            if outcome in (APPLY_INSERTED, APPLY_UPDATED):
                healed += 1
            if use_conditional:
                self._conditional_get_fetched += 1
                await self._store_feed_etag(session, descriptor, item.entity_id, new_etag)
        return processed, healed

    async def replay_from_floor(
        self, session: AsyncSession, *, now: datetime, limit: int = 100
    ) -> ReplayResult:
        """Re-read a trailing window of the changes feed and re-apply each item (usa-wa#159).

        The dropped-event backstop that replaces the O(cohort) anchored scan as the
        *primary* safety net. PM's changes feed is monotonic but **at-least-once, not
        gapless** (power-map#387): a concurrent-commit skip — a lower seq that commits
        *after* the live consumer advanced past it — is never re-delivered incrementally.
        So this re-reads a trailing window each pass and re-applies every item through
        the shared :meth:`_apply_feed_page`. A skipped seq inside the window is
        recovered; an already-current row is an idempotent LWW no-op. O(items in the
        window), not O(cohort) — the feed is subscription-filtered.

        **The window narrows as it converges (usa-wa#211).** The floor is derived by
        :func:`_replay_floor` from the persisted **verified watermark** (this stream's
        ``cursor``: the seq the last completed pass caught up to): steady-state passes
        read from ``verified − replay_retain`` — a small trail sized against PM's
        worst-case in-flight-write span — while only a stream with no watermark
        bootstraps from ``high_water − replay_margin``. Safety argument: every seq is
        first replay-read in the pass that catches up past it, then re-read while it
        stays inside the retained trail; a write whose commit straggles behind its seq
        by more than ``retain`` seqs is the (excluded-by-sizing) residual the anchored
        cohort scan covers. The pre-#211 flat window re-read the same ~10k seqs every
        hour forever and saturated PM's rate limit (117,932 requests / 19h).

        **Bounded passes (usa-wa#211).** A pass stops after ``replay_max_items`` feed
        items (each costs at most one detail GET — a 304 still spends a rate-limit
        token) and persists its stopping seq as the watermark, so the remainder carries
        over losslessly to the next pass. The watermark is monotonic
        (``max(after, verified)``) so an empty tail page can never regress the floor.

        ``high_water`` is read from the *live* ``changes_feed`` cursor (this trails it; it
        never advances it). Horizon fall-off: if the floor sits below PM's oldest-retained
        ``meta.min_seq`` (power-map#388), the pruned ``[floor, min_seq)`` slice cannot be
        replayed — flagged in :class:`ReplayResult.fell_off` so the caller runs a full
        cohort scan for that gap. Bounded by :data:`MAX_RECONCILE_PAGES` against a
        non-advancing PM cursor (the #6 guard shape).
        """
        high_water = _parse_after(await self._read_cursor(session, CHANGES_STREAM)) or 0
        if high_water == 0:
            # The live feed has never advanced its cursor (a fresh/empty deploy, or an
            # empty subscription set): there is no consumed-then-skipped history to
            # recover, and the live feed will bootstrap from seq 0 itself. Skip — reading
            # from floor 0 would (a) re-read the ENTIRE retained feed duplicating that
            # bootstrap and (b) spuriously trip fall-off (0 < min_seq) into a false
            # "replay fell off retention" alert + forced rescan (usa-wa#159 CR-1). Stamp
            # so the cadence applies uniformly; replay engages once the feed advances.
            state = await self._get_or_create_state(session, REPLAY_STREAM)
            state.last_reconcile_at = now
            await session.flush()
            logger.info("powermap_replay_skipped_unbootstrapped", extra={"high_water": high_water})
            return ReplayResult(applied=0, healed=0, fell_off=False, floor=0, high_water=0)
        # The verified watermark (usa-wa#211): the seq the last completed pass caught up
        # to. A garbage cursor parses to 0 (warned by _parse_after) and, like an absent
        # one, falls back to the margin bootstrap — 0 verified history either way.
        verified = _parse_after(await self._read_cursor(session, REPLAY_STREAM)) or 0
        floor = _replay_floor(
            high_water, self._ctx.replay_margin, verified=verified, retain=self._ctx.replay_retain
        )
        after = floor
        applied = 0
        healed = 0
        items = 0
        budget_exhausted = False
        fell_off = False
        pages = 0
        # Pass-level dead-id memory (usa-wa#213): the trailing window often holds many
        # stale items for one gone entity — heal it once, then skip its re-fetches for
        # the rest of this pass (across pages, hence threaded here, not per page).
        dead_ids: set[ULID] = set()
        while True:
            pages += 1
            if pages > MAX_RECONCILE_PAGES:
                logger.warning(
                    "powermap_replay_page_cap",
                    extra={"floor": floor, "after": after, "high_water": high_water},
                )
                break
            page = await self._ctx.read_with_retry(
                lambda after=after: self._ctx.client.get_changes(after, limit=limit),
                log_extra={"read": "replay", "after": after},
            )
            if page.min_seq is not None and floor < page.min_seq and not fell_off:
                # The floor fell off the 90-day retention window: [floor, min_seq) was
                # pruned, so replay cannot cover it. Flag for a full-scan fallback.
                fell_off = True
                logger.warning(
                    "powermap_replay_horizon_fell_off",
                    extra={"floor": floor, "min_seq": page.min_seq, "high_water": high_water},
                )
            # Conditional (usa-wa#160): this window is re-read every pass, so most items
            # are ones we already applied — the case a stored ETag turns into a 304.
            page_processed, page_healed = await self._apply_feed_page(
                session, page, now=now, conditional=True, dead_ids=dead_ids
            )
            applied += page_processed
            healed += page_healed
            items += len(page.items)
            nxt = page.next_after
            if nxt is None or nxt <= after:
                break  # caught up: an empty / non-advancing page is the tail
            after = nxt
            if items >= self._ctx.replay_max_items:
                # Per-pass request budget (usa-wa#211): stop loudly and carry over —
                # ``after`` (persisted below as the watermark) marks the resume point,
                # so the un-read remainder is covered by the next pass, not lost.
                budget_exhausted = True
                logger.info(
                    "powermap_replay_budget_exhausted",
                    extra={
                        "items": items,
                        "max_items": self._ctx.replay_max_items,
                        "after": after,
                        "high_water": high_water,
                    },
                )
                break
        # Stamp the replay stream so the cadence gate waits a full interval — note the
        # deployment overwrites this with the pass END time (``Sidecar
        # ._stamp_replay_completed``, usa-wa#211), which is what actually bounds the duty
        # cycle; this cycle-start value is the deterministic fallback for a direct or
        # ad-hoc caller of this method. Also persist the
        # verified watermark (usa-wa#211) — the seq this pass caught up to (or stopped at
        # on budget exhaustion), which the next pass floors from. Monotonic via max():
        # an immediate-tail pass leaves ``after`` at the floor and must not walk the
        # watermark backwards. Only written here at pass end, so a crash mid-pass
        # re-reads from the previous watermark (at-least-once, never a gap).
        state = await self._get_or_create_state(session, REPLAY_STREAM)
        state.last_reconcile_at = now
        state.cursor = str(max(after, verified))
        await session.flush()
        return ReplayResult(
            applied=applied,
            healed=healed,
            fell_off=fell_off,
            floor=floor,
            high_water=high_water,
            items=items,
            budget_exhausted=budget_exhausted,
        )

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


def _reconcile_stream(descriptor: EntityDescriptor) -> str:
    return f"reconcile:{descriptor.entity_type}"


def _replay_floor(
    high_water: int | None, margin: int, *, verified: int | None = None, retain: int = 0
) -> int:
    """The seq the replay backstop re-reads *after* (usa-wa#159/#211), clamped at 0.

    With a **verified watermark** (usa-wa#211: the seq the last completed pass caught up
    to, persisted on ``REPLAY_STREAM.cursor``) the floor is ``verified − retain`` — the
    steady state, where the window is the small in-flight-write trail plus whatever the
    live feed consumed since the last pass, narrowing as the stream converges. Without
    one (``None`` or 0 — a stream that has never completed a pass) it bootstraps at
    ``high_water − margin``, covering the pre-replay history once.

    ``high_water`` is the live feed's persisted cursor (the newest seq the incremental
    consumer has advanced past); ``None`` (a fresh stream that has never run the live
    feed) floors at 0 — replay the whole retained window. The clamp keeps the floor a
    valid ``after`` (never negative); ``margin`` ≥ high_water (or ``retain`` ≥ verified)
    also floors at 0.
    """
    if verified:
        return max(0, verified - retain)
    return max(0, (high_water or 0) - margin)


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
