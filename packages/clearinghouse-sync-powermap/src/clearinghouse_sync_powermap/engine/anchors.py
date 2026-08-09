"""AnchorManager — the PM anchor, the LWW clock, and the two anchor ledgers (#181).

Extracted from ``SyncEngine`` unchanged. This is the layer directly above
:mod:`~clearinghouse_sync_powermap.engine.context`: it depends on the descriptor
protocol and an ``AsyncSession`` and on **nothing else in the package**, which is what
makes the rest of the split acyclic — both the write path and the read path stamp
anchors and both consult the anchor invariant, so this had to be the shared base rather
than a peer of either.

Responsibilities:

- **the anchor itself** — :meth:`AnchorManager.stamp_anchor` (the single clock-preserving
  stamp site, usa-wa#109) and :meth:`AnchorManager.anchor_taken` (the write-side
  one-row-per-anchor guard, usa-wa#86).
- **the LWW clock** — :meth:`AnchorManager.adopt_remote_clock`, the engine-wide guarantee
  that a freshly-cached row does not read as locally-newer.
- **by-anchor lookup** — :meth:`AnchorManager.row_by_anchor`, the generic "which local row
  holds this PM id" the feed's ``deleted`` branch and the merge guard both need.
- **the re-anchor ledger** — :meth:`AnchorManager.record_reanchor` (usa-wa#108).
- **the non-convergence ledger** — :meth:`AnchorManager.track_convergence` (usa-wa#112),
  plus the standing :func:`nonconverging_count` operator surface.

Both ledger methods return a ``bool`` rather than mutating the drain's tally object
(as they did when everything shared one ``self``). The *ledger* is this manager's
concern; the *per-drain counter* is the writer's, so the writer increments its own
``DrainStats`` from the return value. That keeps the two managers free of shared mutable
state — the only per-process state here is :attr:`AnchorManager._warned_nonconverging`,
this manager's own warn-once throttle.
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.client import ObservationResult
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid
from clearinghouse_sync_powermap.engine.context import EngineContext, enrich_fingerprint
from clearinghouse_sync_powermap.models import (
    DISPOSITION_AUTO_ATTACHED,
    AnchorReanchor,
    NonConvergenceState,
    OutboxEntry,
)

logger = get_logger(__name__)


class AnchorManager:
    """Anchor stamping, the LWW clock, and the re-anchor / non-convergence ledgers."""

    def __init__(self, ctx: EngineContext) -> None:
        self._ctx = ctx
        #: Local row ids already surfaced as non-converging this process (#112 CR-3). A
        #: flagged row re-flags on EVERY drain (the churn is by definition repeating), so
        #: without this the #110-sized cohort (305 rows) would emit 305 WARNINGs per drain
        #: and bury the signal. WARNING once per row, INFO thereafter; a restart re-warns
        #: once (acceptable). The per-drain ``DrainStats.non_converging`` tally and the
        #: standing count stay unthrottled.
        self._warned_nonconverging: set = set()

    # --- the anchor + the clock -----------------------------------------------

    def stamp_anchor(self, descriptor: EntityDescriptor, row: object, pm_id: Any) -> None:
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

    def adopt_remote_clock(
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

    async def row_by_anchor(
        self, session: AsyncSession, descriptor: EntityDescriptor, pm_id: Any
    ) -> Any | None:
        """The local row anchored to ``pm_id``, or None. Generic by-anchor lookup so
        the feed's ``deleted`` branch can find a row from a bare entity id (no record)."""
        if pm_id is None:
            return None
        return await session.scalar(
            select(descriptor.model).where(descriptor.anchor_column_expr() == as_ulid(pm_id))
        )

    async def anchor_taken(
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
        (``OutboxWriter._deliver``) parks the offending entry, and the sweep's PM-first
        adoption (``OutboxWriter._sweep_row``) declines the adopt and falls through to a
        CREATE so the drain owns the single park. Autoflush makes a same-transaction
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

    # --- the re-anchor ledger (usa-wa#108) ------------------------------------

    async def record_reanchor(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        row: Any,
        result: ObservationResult,
    ) -> bool:
        """Capture an in-place anchor **overwrite** before it destroys the old id (#108).

        PM dedups assignments on ``(person, role, start_date)``, so a producer's
        start-date correction re-produces an observation that no longer matches the
        stored key: PM mints a *fresh* assignment (disposition ``new``) and returns its
        id, while the assignment our anchor still points at is silently orphaned upstream.
        :meth:`stamp_anchor` then overwrites ``pm_*_id`` in place — so the old id, the
        only handle on the orphan, is gone the instant the stamp lands.

        This runs *before* that stamp whenever the delivered id differs from the anchor
        the row already carries, and does two things the overwrite would otherwise lose:
        a WARNING (the alert) and a durable :class:`AnchorReanchor` ledger row (the
        queryable, retained record the orphan-reconcile cleanup reads once power-map#311
        ships). A first-time stamp (row had no anchor — an ordinary CREATE) is not an
        overwrite and is skipped; a re-delivery returning the *same* id is a no-op.

        Generic across entity types: any anchored row re-resolving to a different id is
        an orphan-minting overwrite, whatever the match semantics that caused it.

        Returns True iff a ledger row was written, so the caller can tally it on its own
        ``DrainStats`` (#181: the ledger is this manager's, the per-drain counter is the
        writer's).
        """
        old = descriptor.anchor_value(row)
        if old is None or old == result.pm_id:
            return False  # first anchor, or an unchanged re-observe — no orphan
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
        return True

    # --- the non-convergence ledger (usa-wa#112) ------------------------------

    async def track_convergence(
        self,
        session: AsyncSession,
        descriptor: EntityDescriptor,
        row: Any,
        entry: OutboxEntry,
        result: ObservationResult,
        old_anchor: Any,
        payload: dict,
    ) -> bool:
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
        ``OutboxWriter.maybe_enqueue_enrich`` sees ``identifier_missing`` — that trigger is
        *not* fingerprint-gated, so a row whose identifier PM persistently fails to adopt
        re-sends an identical enrich payload every reconcile and is flagged. That is intended
        coverage, not a false positive: it is a genuine non-convergence. A *drift*-triggered
        enrich cannot climb, since a changed payload resets the counter by construction.

        Detection-only (usa-wa#112 Phase A): the row keeps delivering, so no ``UNAVAILABLE``
        park and no false-park risk — the re-POST cost is already bounded by the PM
        min-interval governor (#85) and the 12h reconcile cadence, and the harm the #110
        audit found was *silence*, not cost. A park + enqueue-side re-arm is a deferred
        Phase B, warranted only if the standing count ever shows a cohort large enough for
        the governed cost to matter.

        Returns True iff this delivery pushed the row to/over the threshold, so the caller
        can tally it on its own ``DrainStats`` (#181).
        """
        if old_anchor is None:
            # First attach — nothing to accrue, and no state row can exist yet. Return before
            # the query so the bulk-produce CREATE path pays neither a SELECT nor an autoflush.
            return False
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
            return False
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
        if state.count < self._ctx.nonconvergence_threshold:
            return False
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
            return True
        self._warned_nonconverging.add(row.id)
        logger.warning("observation_not_converging", extra=extra)
        return True

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


async def nonconverging_count(session: AsyncSession, *, threshold: int) -> int:
    """Rows currently at/over the non-convergence threshold (usa-wa#112).

    The standing set of rows PM keeps ``auto-attached``-matching without applying our
    diff — an identical payload re-sent every reconcile cycle. Free function like
    :func:`~clearinghouse_sync_powermap.engine.write.rejected_breakdown` so the sidecar's
    cycle summary reads it without an engine and alerts on a rise (the #85 pattern). A row
    converges (a real edit lands, or PM finally applies) → its counter resets to 0 → it
    drops out of this count.
    """
    return (
        await session.scalar(
            select(func.count())
            .select_from(NonConvergenceState)
            .where(NonConvergenceState.count >= threshold)
        )
    ) or 0
