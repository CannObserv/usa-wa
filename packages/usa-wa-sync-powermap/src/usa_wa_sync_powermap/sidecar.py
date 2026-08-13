"""The long-running sidecar — drives the engine one cycle at a time.

Process model B (single daemon). Each cycle: run the due subscription re-discovery
backstop (register/backfill new WA-subtree entities), pull the subscription-filtered
changes feed, run the due reconcile backstops (jurisdictions: none; cohort producers:
the bounded anchored-cohort re-fetch that recovers dropped feed events — usa-wa#13),
sweep un-anchored rows, and drain the outbox. The legacy full-list reconcile is
retired for usa-wa but the generic hook remains for siblings.

Per-cycle isolation (CR #13): every cycle runs in its own session inside a
try/except that rolls back and logs on failure, so a propagating non-transient
error (the outbox worker no longer swallows bugs as transient) cannot kill the
daemon or poison the next cycle.

Cycle-failure containment (#85, from the #84 postmortem): each descriptor's
reconcile runs in its OWN session + error boundary (``_run_reconciles``), so one
poison entity cannot roll back the other descriptors' reconcile stamps, the feed
cursor, or the drain. Isolation must not defeat the failure signal, though: every
contained component failure (catalog sync, backstop, a descriptor reconcile, the
tick) fails the cycle *verdict* — ``run_cycle`` returns False — which drives the
exponential backoff (``retry.backoff``, 60s base → 1h cap) and the failure-streak
operator alert in :meth:`run_forever`. The sidecar is a ``Restart=`` service the
#49 ``OnFailure=`` handler can't see, so after ``failure_alert_threshold``
consecutive failed cycles the injected ``alert`` callable emails the operator once
per streak (re-armed by the next clean cycle).

Outbox delivery transaction boundary (#8): the read + sweep work runs in one
session, but the outbox *drain* commits incrementally — by default once per
delivered entry (``outbox_commit_chunk_size = 1``), so a slow PM never holds one
open DB transaction across N network round-trips. The chunk size is configurable
(``SidecarSettings.outbox_commit_chunk_size``) to amortise commit cost when
throughput dominates over lock-hold latency. ``run_cycle`` passes the session's
commit as the drain's commit hook and issues a final commit for the read/sweep
work (and any sub-chunk drain remainder).
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.engine import (
    DEFAULT_NONCONVERGENCE_THRESHOLD,
    REPLAY_STREAM,
    DrainStats,
    ReplayResult,
    SyncEngine,
    nonconverging_count,
    outbox_backlog,
    rejected_breakdown,
)
from clearinghouse_sync_powermap.models import SyncState
from clearinghouse_sync_powermap.retry import backoff
from clearinghouse_sync_powermap.subscriptions import SubscriptionReconciler

logger = get_logger(__name__)

#: SyncState stream tracking the last in-loop re-discovery backstop run.
SUBSCRIPTIONS_STREAM = "subscriptions"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Sidecar:
    """Drives :class:`SyncEngine` cycles over the usa-wa descriptor registry."""

    def __init__(
        self,
        engine: SyncEngine,
        descriptors: Sequence[EntityDescriptor],
        session_factory: async_sessionmaker[AsyncSession],
        *,
        feed_poll_seconds: float = 60.0,
        reconciler: SubscriptionReconciler | None = None,
        # Library default; the deployment value lives in SidecarSettings (6h, #73 Axis 2)
        # and is always passed explicitly by __main__ — this 1h only applies to a bare
        # Sidecar() (tests, which override it anyway).
        subscription_backstop_cadence: timedelta = timedelta(hours=1),
        outbox_commit_chunk_size: int = 1,
        catalog_sync: Callable[[AsyncSession], Awaitable[Any]] | None = None,
        catalog_sync_cadence: timedelta = timedelta(hours=1),
        alert: Callable[[str, str], Awaitable[None]] | None = None,
        failure_alert_threshold: int = 5,
        # Must MATCH the engine's ``nonconvergence_threshold`` (#112 CR-13): the engine flags
        # rows at its own value while this one drives the standing query, so a divergence
        # would make the cycle summary and the per-row WARNINGs disagree. ``__main__`` passes
        # ``settings.nonconvergence_threshold`` to both; nothing structurally enforces it.
        nonconvergence_threshold: int = DEFAULT_NONCONVERGENCE_THRESHOLD,
        # Changes-feed replay backstop (usa-wa#159): the kill switch + cadence for the
        # trailing re-read (its margin lives on the engine). Library defaults; __main__
        # passes the SidecarSettings values. Phase A runs it alongside the unchanged 12h
        # anchored scan (shadow) — the scan widens to weekly only once the replay
        # would-heal delta proves coverage (Phase B).
        replay_enabled: bool = True,
        replay_cadence: timedelta = timedelta(hours=1),
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if nonconvergence_threshold < 1:
            # Mirrors the engine's guard (#112 CR-1/CR-11). Production builds the engine
            # first, so a bad env already fails there — this keeps a bare Sidecar (a test, a
            # future caller) from silently getting the inverted standing query, where
            # ``count >= 0`` matches every *reset* row and the rise-alert names the cohort.
            raise ValueError("nonconvergence_threshold must be >= 1")
        self._engine = engine
        self._descriptors = list(descriptors)
        self._session_factory = session_factory
        self._feed_poll_seconds = feed_poll_seconds
        self._reconciler = reconciler
        self._subscription_backstop_cadence = subscription_backstop_cadence
        self._outbox_commit_chunk_size = outbox_commit_chunk_size
        # Role-type catalog refresh (power-map#268): keeps the local RoleType mirror the
        # RoleDescriptor reads current. Runs on the first cycle (so seats can flow after
        # startup) then on ``catalog_sync_cadence``. In-memory cadence — a restart
        # re-syncs, which is the freshness we want.
        self._catalog_sync = catalog_sync
        self._catalog_sync_cadence = catalog_sync_cadence
        self._last_catalog_sync: datetime | None = None
        # Failure-streak alerting (#85): the sidecar is a Restart= service, invisible
        # to the #49 OnFailure= handler, so it emails the operator itself. The send
        # is an injected callable (deployment wires the exe.dev gateway; tests fake it).
        self._alert = alert
        self._failure_alert_threshold = failure_alert_threshold
        # Component errors collected during the current run_cycle — embedded in the
        # streak alert body so the operator can triage without opening the journal.
        self._cycle_errors: list[str] = []
        # Last observed REJECTED backlog count (#85 rejection visibility): the
        # cycle summary alerts only when the count RISES past this, so a standing
        # pile emails once (and once more per restart — deliberate: a pile needs a
        # data fix, and a process that never saw it should say so).
        self._last_rejected_count = 0
        # Non-convergence backstop (usa-wa#112): the threshold of consecutive identical
        # auto-attached re-sends that flags a row, and the last observed standing count of
        # flagged rows. The cycle summary alerts on a RISE past this (the #85 pattern), so
        # a churning cohort that went unnoticed for days is now surfaced + emailed on
        # arrival; a static set does not re-spam.
        self._nonconvergence_threshold = nonconvergence_threshold
        self._last_nonconverging_count = 0
        # Last drain's disposition + re-anchor tallies (usa-wa#108), captured off the
        # engine at the end of each tick and surfaced in the cycle summary — so a burst
        # of `new` dispositions / anchor overwrites (orphan mints) is countable at a
        # glance. Empty default so a summary before the first tick is safe.
        self._last_drain_stats = DrainStats()
        # Changes-feed replay backstop (usa-wa#159): config + the last pass's result for
        # the cycle summary, and the latched fall-off state so the horizon-loss alert
        # fires once on the rising edge (the #85 rise-alert shape), not every cycle.
        self._replay_enabled = replay_enabled
        self._replay_cadence = replay_cadence
        self._last_replay_result: ReplayResult | None = None
        self._last_replay_fell_off = False
        # Whether a replay pass actually executed this cycle (vs disabled / not-due).
        # Replay runs hourly but the summary logs every ~minute, so this gates the
        # replay_* summary fields + the fall-off latch to *actual* passes — otherwise
        # they'd repeat the last pass's numbers on every intervening cycle (#159 CR-3).
        self._replay_ran_this_cycle = False
        # Conditional-GET tallies from this cycle's reconciles (usa-wa#160), captured off
        # the engine after _run_reconciles for the cycle summary — a high skipped:fetched
        # ratio is the win (most anchored rows 304 instead of a full re-fetch).
        self._last_conditional_get: tuple[int, int] = (0, 0)
        self._clock = clock

    async def tick(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """One sync cycle against a single session.

        When ``commit`` is supplied, both the *sweep* (per keyset batch, #92) and the
        outbox *drain* (per delivered entry by default, or every
        ``outbox_commit_chunk_size`` entries, #8) commit incrementally — so a slow or
        rate-limiting PM never holds the transaction open across the whole cohort, and
        a first bulk ingest's progress persists batch-by-batch instead of riding one
        all-or-nothing transaction. The feed read accumulates into the first such
        boundary. With no ``commit`` hook the whole tick is one transaction (the legacy
        boundary).

        Neither the subscription re-discovery backstop nor the per-descriptor
        reconciles run here — each runs in its own session via :meth:`run_cycle`
        (#85), so a discovery/PM failure or one poison entity cannot roll back or
        starve the feed/sweep/drain in this transaction.
        """
        # Reset the drain tallies up front (usa-wa#108): if this tick raises after a
        # partial drain, the cycle summary must report *this* cycle (empty) rather than
        # attribute the previous cycle's mint counts to a failed one. The end-of-tick
        # capture overwrites with the real drain stats on success.
        self._last_drain_stats = DrainStats()
        # Read: the incremental feed (the real-time path).
        await self._engine.process_feed(session, now=now)
        # Writes: enqueue un-anchored rows, then deliver.
        for descriptor in self._descriptors:
            if descriptor.write_enabled:
                await self._engine.sweep_unanchored(session, descriptor, commit=commit)
        # Drain against a FRESH clock read (#93): the sweep above can take minutes on a
        # bulk ingest, so entries enqueued during it carry a ``next_attempt_at`` later than
        # the cycle-start ``now`` — draining against the stale ``now`` finds them "not due"
        # and defers delivery a whole cycle. A fresh read is >= every just-enqueued stamp.
        await self._engine.drain_outbox(
            session,
            now=self._clock(),
            commit=commit,
            chunk_size=self._outbox_commit_chunk_size,
        )
        # Capture this drain's tallies for the cycle summary (usa-wa#108). The summary
        # runs later in its own session, so it reads the stashed value, not the engine.
        self._last_drain_stats = self._engine.last_drain_stats

    async def run_cycle(self) -> bool:
        """Run one isolated cycle; return the cycle verdict (True = fully clean).

        Component order: catalog sync → re-discovery backstop → per-descriptor
        reconciles → replay backstop → main tick (feed → sweep → drain). Each
        component runs in its own session + error boundary, so any one failing
        leaves the rest running.
        Containment must not defeat the retry-pressure signal (#85): any contained
        component failure flips the verdict to False, which :meth:`run_forever`
        turns into exponential backoff + streak alerting.
        """
        now = self._clock()
        self._cycle_errors = []
        self._replay_ran_this_cycle = False
        # Per-cycle conditional-GET tally (#160). Guarded for the engine=None test doubles;
        # production always has an engine.
        if self._engine is not None:
            self._engine.reset_conditional_get_stats()
        ok = await self._run_catalog_sync(now)
        ok = await self._run_backstop(now) and ok
        ok = await self._run_reconciles(now) and ok
        ok = await self._run_replay(now) and ok
        # Capture the conditional-GET tally (it accumulates on the shared engine across
        # the reconciles' per-descriptor sessions) for the cycle summary (#160). Read
        # *after* the replay, not between: since the #160 residual the replay backstop is
        # conditional too, and capturing before it would report its 304s a cycle late.
        if self._engine is not None:
            self._last_conditional_get = self._engine.conditional_get_stats
        async with self._session_factory() as session:
            try:
                # The drain commits incrementally via this hook (#8); the trailing
                # commit covers the read/sweep work and any sub-chunk drain remainder.
                await self.tick(session, now=now, commit=session.commit)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.exception("sidecar_cycle_failed")
                self._cycle_errors.append(f"tick: {exc!r}")
                ok = False
        await self._report_cycle_summary(now)
        return ok

    async def _report_cycle_summary(self, now: datetime) -> None:
        """Run the cycle summary in its own session; never affects the verdict.

        Observability, not work (#85): a summary-query failure must not flip the
        cycle verdict — that would put reporting in the backoff/alert path.
        """
        try:
            async with self._session_factory() as session:
                await self.report_cycle_summary(session, now=now)
        except Exception:
            logger.exception("sidecar_cycle_summary_failed")

    async def report_cycle_summary(self, session: AsyncSession, *, now: datetime) -> None:
        """Log the outbox backlog + REJECTED reason breakdown; alert on a rise (#85).

        A REJECTED observation is a single ``logger.error`` at park time and then
        silence — the #84 postmortem found 12 ``identifier_conflict`` rejections
        that sat unnoticed for a week. This re-surfaces the standing pile every
        cycle (the ``sidecar_cycle_summary`` line) and emails the operator when the
        count *rises* past the last observed count — a static pile never re-spams,
        a genuinely new rejection after a fix alerts again. Distinct from the
        failure-streak alert (cycle crashes, not per-entry verdicts).
        """
        backlog = await outbox_backlog(session, now=now)
        reasons = await rejected_breakdown(session)
        non_converging = await nonconverging_count(
            session, threshold=self._nonconvergence_threshold
        )
        stats = self._last_drain_stats
        # Report the replay numbers only on a cycle where a pass actually ran (#159 CR-3);
        # replay is hourly while this summary is ~per-minute, so a stale last-pass value
        # would otherwise repeat on every intervening cycle and read as per-cycle.
        replay = self._last_replay_result if self._replay_ran_this_cycle else None
        logger.info(
            "sidecar_cycle_summary",
            extra={
                "pending": backlog.pending,
                "pending_due": backlog.pending_due,
                "rejected": backlog.rejected,
                "unavailable": backlog.unavailable,
                "oldest_pending_age_seconds": backlog.oldest_pending_age_seconds,
                "rejected_reasons": reasons,
                # Last drain's PM verdicts + orphan-mint count (usa-wa#108): a plain dict
                # so it renders in the structured log; ``reanchors`` > 0 = orphaned PM ids
                # this cycle (each recorded in ``powermap_anchor_reanchor``).
                "dispositions": dict(stats.dispositions),
                "reanchors": stats.reanchors,
                # Deltas PM withheld on a natural-key auto-attach (usa-wa#111/power-map#311b);
                # should stay 0 now anchored rows are id-addressed, a rise is the signal.
                "unapplied": stats.unapplied,
                # Rows PM keeps auto-attach-matching without applying our diff (usa-wa#112):
                # the standing count at/over the threshold — an identical payload re-sent
                # every reconcile cycle. Alerts on a rise, like the REJECTED pile.
                "non_converging": non_converging,
                # Changes-feed replay backstop (usa-wa#159): the last pass's would-heal
                # delta (``replay_healed`` > 0 = replay recovered events the live feed
                # dropped — its reason to exist) and horizon fall-off. None until the first
                # replay runs this process.
                "replay_healed": replay.healed if replay else None,
                "replay_applied": replay.applied if replay else None,
                "replay_fell_off": replay.fell_off if replay else None,
                # Conditional GET on the reconcile (usa-wa#160): rows 304-skipped vs.
                # re-fetched full this cycle. A high skipped share = the bandwidth/DB win.
                "conditional_get_skipped": self._last_conditional_get[0],
                "conditional_get_fetched": self._last_conditional_get[1],
            },
        )
        if backlog.rejected > self._last_rejected_count:
            await self._send_rejected_alert(backlog.rejected, reasons)
        self._last_rejected_count = backlog.rejected
        if non_converging > self._last_nonconverging_count:
            await self._send_nonconverging_alert(non_converging)
        self._last_nonconverging_count = non_converging
        # Horizon fall-off (usa-wa#159 / power-map#388): the replay floor sat below PM's
        # oldest-retained seq, so a slice of history was pruned before we replayed it —
        # potential un-recovered data loss beyond the 90-day window. Alert once on the
        # rising edge (the anchored scan still covers the gap in Phase A); re-arm when it
        # clears so a later recurrence alerts again. Only evaluate on a cycle where replay
        # actually ran (#159 CR-3) — a non-run cycle carries no fresh verdict, so touching
        # the latch would spuriously re-arm it and re-alert on the next real fall-off.
        if replay is not None:
            if replay.fell_off and not self._last_replay_fell_off:
                await self._send_replay_fell_off_alert(replay)
            self._last_replay_fell_off = replay.fell_off

    async def _send_rejected_alert(self, rejected: int, reasons: dict[str, int]) -> None:
        """Email the operator about a REJECTED-count rise; swallow send failures."""
        if self._alert is None:
            logger.warning("sidecar_rejected_rise_unalerted", extra={"rejected": rejected})
            return
        subject = (
            f"[usa-wa] sidecar rejected observations: {rejected}"
            f" (rose from {self._last_rejected_count})"
        )
        lines = "\n".join(f"{count} x {reason}" for reason, count in sorted(reasons.items()))
        body = (
            f"The PM sync outbox holds {rejected} REJECTED observation(s) — PM refused\n"
            f"the payload, so each needs a data fix (the next sweep re-attempts fixed\n"
            f"rows automatically). No repeat email while the count is static.\n\n"
            f"--- reasons ---\n{lines or '(none recorded)'}\n"
        )
        try:
            await self._alert(subject, body)
        except Exception:
            logger.exception("sidecar_rejected_alert_failed", extra={"rejected": rejected})

    async def _send_nonconverging_alert(self, non_converging: int) -> None:
        """Email the operator about a rise in non-converging rows; swallow send failures.

        A non-converging row (usa-wa#112) is one PM keeps ``auto-attached``-matching without
        applying our diff — an identical observation re-sent every reconcile cycle forever.
        The #110 audit found three such cohorts, each unnoticed for days until a manual
        outbox scan. This surfaces a fourth on arrival. Detection-only: the row keeps
        delivering (no park), so the fix is to investigate the diff PM refuses (a classifier
        drift, a merged-state conflict) — the ``observation_not_converging`` WARNING names
        each offending row.
        """
        if self._alert is None:
            logger.warning(
                "sidecar_nonconverging_rise_unalerted", extra={"non_converging": non_converging}
            )
            return
        subject = (
            f"[usa-wa] sidecar non-converging rows: {non_converging}"
            f" (rose from {self._last_nonconverging_count})"
        )
        body = (
            f"The PM sync has {non_converging} row(s) PM keeps auto-attach-matching\n"
            f"without applying our diff — an identical observation re-sent every reconcile\n"
            f"cycle (threshold {self._nonconvergence_threshold} consecutive re-sends). Each\n"
            f"is a silent producer→PM non-convergence.\n\n"
            f"Grep the journal for `observation_not_converging` (logged once per row when it\n"
            f"is first flagged) and `observation_still_not_converging` (the throttled INFO\n"
            f"repeat every drain thereafter) to see which rows + what field PM refuses.\n"
            f"No repeat email while the count is static.\n"
        )
        try:
            await self._alert(subject, body)
        except Exception:
            logger.exception(
                "sidecar_nonconverging_alert_failed", extra={"non_converging": non_converging}
            )

    async def _send_replay_fell_off_alert(self, replay: ReplayResult) -> None:
        """Email the operator when the replay floor fell off PM's retention window (#159).

        A fall-off means ``[floor, min_seq)`` was pruned from PM's 90-day outbox before
        the replay re-read it — any event in that slice the live feed also skipped is
        un-recovered by replay. In Phase A the anchored scan still covers it; the alert is
        the signal to widen the replay margin or shorten its cadence so the floor stays
        inside the window. Swallow send failures (never crash the loop being watched).
        """
        if self._alert is None:
            logger.warning(
                "sidecar_replay_fell_off_unalerted",
                extra={"floor": replay.floor, "high_water": replay.high_water},
            )
            return
        subject = "[usa-wa] sidecar changes-feed replay fell off the retention window"
        body = (
            f"The changes-feed replay backstop (usa-wa#159) read from seq {replay.floor},\n"
            f"which is below PM's oldest-retained seq (meta.min_seq, power-map#388) — the\n"
            f"pruned slice could not be replayed. The anchored-cohort scan still covers it\n"
            f"for now, but the replay margin is too small or its cadence too slow: widen\n"
            f"REPLAY_MARGIN or shorten REPLAY_CADENCE so the floor stays inside the 90-day\n"
            f"window. high_water={replay.high_water}, floor={replay.floor}.\n"
        )
        try:
            await self._alert(subject, body)
        except Exception:
            logger.exception("sidecar_replay_fell_off_alert_failed", extra={"floor": replay.floor})

    async def _run_reconciles(self, now: datetime) -> bool:
        """Run each due descriptor's reconcile in its own session + error boundary.

        The #84 amplification fix (#85 fix 1): the assignment descriptor's poison
        entity rolled back the whole tick — the other descriptors' reconcile stamps,
        the feed cursor — and aborted before the drain. Here a raising reconcile is
        contained to its descriptor: its own session rolls back (the context manager
        discards uncommitted work), the others' reconciles commit, and the tick still
        runs. The failed descriptor's stream stays unstamped, so it is due again next
        cycle — retry frequency is bounded by :meth:`run_forever`'s backoff, not here.

        Returns False if any descriptor's reconcile failed (the cycle-verdict signal).
        """
        ok = True
        for descriptor in self._descriptors:
            try:
                async with self._session_factory() as session:
                    await self.run_descriptor_reconcile(session, descriptor, now=now)
            except Exception as exc:
                logger.exception(
                    "sidecar_reconcile_failed",
                    extra={"entity_type": descriptor.entity_type},
                )
                self._cycle_errors.append(f"reconcile:{descriptor.entity_type}: {exc!r}")
                ok = False
        return ok

    async def run_descriptor_reconcile(
        self, session: AsyncSession, descriptor: EntityDescriptor, *, now: datetime
    ) -> bool:
        """Due-check → reconcile backstop → commit, on the given ``session``.

        Returns True if the reconcile was due and ran. Jurisdictions run none
        (subscription feed + discovery only); the cohort producers run the bounded
        anchored-cohort backstop (re-fetch our anchored rows → recover dropped feed
        events, usa-wa#13); the full-list backstop is sibling-only. The commit hook
        bounds the open transaction to one page of PM round-trips (#13 CR); the
        trailing commit persists the reconcile stamp. Separated from
        :meth:`_run_reconciles` as the testable seam (the ``run_subscription_backstop``
        pattern); production calls it via ``_run_reconciles``, which adds the session
        isolation + error containment.
        """
        if not await self._reconcile_due(session, descriptor, now):
            return False
        await self._engine.reconcile(session, descriptor, now=now, commit=session.commit)
        await session.commit()
        return True

    async def _run_replay(self, now: datetime) -> bool:
        """Run the changes-feed replay backstop in its own session + error boundary (#159).

        The trailing re-read that re-covers PM's at-least-once concurrent-commit skip
        (power-map#387) — the primary dropped-event backstop the O(cohort) anchored scan
        is being retired *toward*. Isolated like :meth:`_run_reconciles`: a failing replay
        rolls back only its own session (its cadence stays unstamped → due again next
        cycle, retry bounded by :meth:`run_forever` backoff) and flips the cycle verdict,
        but cannot roll back the reconciles or starve the tick. Cadence-gated
        (:meth:`_replay_due`) on ``REPLAY_STREAM``; ``replay_enabled=False`` skips it
        entirely (a clean verdict — a disabled backstop is not a failure).

        Phase A (shadow): this runs *alongside* the unchanged 12h anchored scan and its
        ``ReplayResult`` is recorded for the cycle summary's would-heal delta. On horizon
        fall-off it logs + latches for the summary alert; the anchored scan is the live
        safety net for the pruned slice until Phase B widens its cadence.
        """
        if not self._replay_enabled:
            return True
        try:
            async with self._session_factory() as session:
                if not await self._replay_due(session, now):
                    return True
                started = self._clock()
                result = await self._engine.replay_from_floor(session, now=now)
                # End-stamp the cadence (usa-wa#211): the engine stamped the cycle-start
                # ``now``, but a pass approaching the cadence would then be due again
                # almost immediately — the silent duty-cycle stacking that held PM at
                # its rate limit for 19h. Re-stamping with the pass END guarantees a
                # full cadence of idle PM time between passes, whatever a pass costs.
                ended = self._clock()
                await self._stamp_replay_completed(session, ended)
                if result.fell_off:
                    # The replay floor fell off PM's retention window: a pruned slice
                    # can't be replayed, so force the anchored-cohort scan due now rather
                    # than wait out its (Phase-B: weekly) cadence — it is the only backstop
                    # that covers the gap. Effective next cycle (this runs after the
                    # reconciles); the alert on the summary is the operator-facing signal.
                    await self._force_anchored_rescan(session)
                await session.commit()
            # Set the "ran" flag together with the result, only after a clean commit
            # (#159 CR-4): a raising replay/commit must leave the flag False so the summary
            # reports None rather than the previous pass's stale numbers as if fresh.
            self._replay_ran_this_cycle = True
            self._last_replay_result = result
            duration = (ended - started).total_seconds()
            if ended - started >= self._replay_cadence:
                # The loud half of the #211 duty-cycle rule: end-stamping makes
                # back-to-back stacking structurally impossible, and a pass that ran a
                # whole cadence is an operator-visible anomaly, not a silent steady state.
                logger.warning(
                    "sidecar_replay_overrun",
                    extra={
                        "duration_seconds": duration,
                        "cadence_seconds": self._replay_cadence.total_seconds(),
                        "items": result.items,
                    },
                )
            logger.info(
                "sidecar_replay",
                extra={
                    "applied": result.applied,
                    "healed": result.healed,
                    "fell_off": result.fell_off,
                    "floor": result.floor,
                    "high_water": result.high_water,
                    # usa-wa#211: the per-pass request budget, observable — items is the
                    # enumeration count (≤ one detail GET each; 304s/404s still spend a
                    # rate-limit token), budget_exhausted marks a carried-over pass.
                    "items": result.items,
                    "budget_exhausted": result.budget_exhausted,
                    "duration_seconds": duration,
                },
            )
            return True
        except Exception as exc:
            logger.exception("sidecar_replay_failed")
            self._cycle_errors.append(f"replay: {exc!r}")
            return False

    async def _stamp_replay_completed(self, session: AsyncSession, ended: datetime) -> None:
        """Overwrite ``REPLAY_STREAM.last_reconcile_at`` with the pass END time (#211).

        The engine stamps the caller's ``now`` (the cycle start) for determinism; the
        deployment's cadence policy is that the interval runs from pass *completion*,
        so a pass can never make itself immediately due again however long it ran —
        the duty cycle is bounded at ``duration / (duration + cadence)`` < 100% by
        construction. Mirrors :meth:`_mark_subscription_synced`.
        """
        state = (
            await session.execute(select(SyncState).where(SyncState.stream == REPLAY_STREAM))
        ).scalar_one_or_none()
        if state is None:
            state = SyncState(stream=REPLAY_STREAM)
            session.add(state)
        state.last_reconcile_at = ended
        await session.flush()

    async def _force_anchored_rescan(self, session: AsyncSession) -> None:
        """Clear the anchored-cohort reconcile stamps so the full scan runs next cycle (#159).

        The replay backstop's fall-off fallback: when a pruned slice can't be replayed,
        the O(cohort) anchored scan is the only cover, so make it due immediately by
        nulling each ``anchored_cohort`` descriptor's ``reconcile:{type}`` stamp (and its
        keyset cursor, forcing a fresh full pass). This is what keeps the Phase-B *weekly*
        scan cadence safe — a fall-off self-heals within a cycle instead of waiting a week.
        No-op if no descriptor uses the anchored-cohort backstop.
        """
        # Mirrors _reconcile_due's stream key: f"reconcile:{entity_type}".
        streams = [
            f"reconcile:{d.entity_type}"
            for d in self._descriptors
            if getattr(d, "reconcile_mode", None) == "anchored_cohort"
        ]
        if not streams:
            return
        rows = (
            (await session.execute(select(SyncState).where(SyncState.stream.in_(streams))))
            .scalars()
            .all()
        )
        for row in rows:
            row.last_reconcile_at = None
            row.cursor = None
        logger.warning("powermap_replay_forcing_full_rescan", extra={"streams": streams})

    async def _replay_due(self, session: AsyncSession, now: datetime) -> bool:
        """Whether the replay backstop should run this cycle (#159).

        Due immediately on first run (no stamp), then every ``replay_cadence``. Mirrors
        :meth:`_subscription_backstop_due`, keyed on ``REPLAY_STREAM``'s
        ``last_reconcile_at`` (stamped by :meth:`SyncEngine.replay_from_floor`).
        """
        state = (
            await session.execute(select(SyncState).where(SyncState.stream == REPLAY_STREAM))
        ).scalar_one_or_none()
        if state is None or state.last_reconcile_at is None:
            return True
        return (now - state.last_reconcile_at) >= self._replay_cadence

    async def _run_catalog_sync(self, now: datetime) -> bool:
        """Refresh the role-type catalog mirror in its own session + error boundary.

        Runs on the first cycle and thereafter on ``catalog_sync_cadence`` (in-memory).
        Isolated like :meth:`_run_backstop` so a catalog-fetch/PM failure can't roll back
        or starve the main tick; a failure leaves the cadence unstamped so the next cycle
        retries promptly. The mirror gates seat observations (:class:`RoleDescriptor`), so
        a stale-but-present mirror is safe — seats simply keep flowing on the last catalog.

        Returns False on a contained failure (the cycle-verdict signal, #85)."""
        if self._catalog_sync is None:
            return True
        if (
            self._last_catalog_sync is not None
            and now - self._last_catalog_sync < self._catalog_sync_cadence
        ):
            return True
        try:
            async with self._session_factory() as session:
                await self._catalog_sync(session)
                await session.commit()
            self._last_catalog_sync = now
            return True
        except Exception as exc:
            logger.exception("role_type_catalog_sync_failed")
            self._cycle_errors.append(f"catalog_sync: {exc!r}")
            return False

    async def _run_backstop(self, now: datetime) -> bool:
        """Run the due re-discovery backstop in its own session + error boundary.

        Isolated from :meth:`run_cycle`'s main tick so a discovery/registration failure
        (or a poisoned session from a mid-backfill error) cannot roll back or starve the
        feed/drain. Logs and swallows; the next cycle retries (still gated by cadence on
        success — a failure leaves the stream unstamped, so it retries promptly).

        The ``try`` wraps the whole session lifecycle, so even a failure to *acquire* the
        session (pool exhausted) or to close/roll back it is contained here and cannot
        propagate out of :meth:`run_cycle` to crash the daemon before the feed runs. The
        context manager rolls back any uncommitted work on close, so no explicit rollback
        is needed.

        Returns False on a contained failure (the cycle-verdict signal, #85).
        """
        if self._reconciler is None:
            return True
        try:
            async with self._session_factory() as session:
                if await self.run_subscription_backstop(session, now=now):
                    await session.commit()
            return True
        except Exception as exc:
            logger.exception("subscription_backstop_failed")
            self._cycle_errors.append(f"subscription_backstop: {exc!r}")
            return False

    async def run_subscription_backstop(self, session: AsyncSession, *, now: datetime) -> bool:
        """Due-check → discover/register/backfill → stamp, on the given ``session``.

        Returns True if the backstop was due and ran (so the caller commits). Separated
        from :meth:`_run_backstop` as the testable seam; production calls it via
        ``_run_backstop``, which adds the session isolation + error containment.
        """
        if self._reconciler is None or not await self._subscription_backstop_due(session, now):
            return False
        await self._reconciler.sync_subscriptions(session)
        await self._mark_subscription_synced(session, now)
        return True

    async def run_forever(
        self, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        """Loop cycles forever, backing off on consecutive failures (#85).

        A clean cycle sleeps ``feed_poll_seconds``. A failed cycle sleeps
        ``max(feed_poll_seconds, backoff(streak))`` — the outbox retry schedule
        (60s base, doubling, 1h cap) — so a deterministic poison entity retries
        hourly, not every poll (the #84 amplification: ~2.6 min forever). A success
        resets the streak.

        At ``failure_alert_threshold`` consecutive failures the injected ``alert``
        callable emails the operator once — no repeat while the streak continues;
        a clean cycle re-arms it. A failed send is swallowed (never crash the loop
        being watched); the failure is already in the journal.
        """
        logger.info(
            "sidecar_started",
            extra={"entities": [d.entity_type for d in self._descriptors]},
        )
        streak = 0
        while True:
            ok = await self.run_cycle()
            if ok:
                streak = 0
                delay = self._feed_poll_seconds
            else:
                streak += 1
                if streak == self._failure_alert_threshold:
                    await self._send_streak_alert(streak)
                delay = max(self._feed_poll_seconds, backoff(streak).total_seconds())
            await sleep(delay)

    async def _send_streak_alert(self, streak: int) -> None:
        """Email the operator about a failure streak; swallow send failures."""
        if self._alert is None:
            logger.warning("sidecar_failure_streak_unalerted", extra={"streak": streak})
            return
        subject = f"[usa-wa] sidecar cycle failure streak ({streak} consecutive)"
        errors = "\n".join(self._cycle_errors) or "(no component errors captured)"
        body = (
            f"The PM sync sidecar has failed {streak} consecutive cycles.\n"
            f"Retries continue with exponential backoff (1h cap); no repeat email\n"
            f"while this streak continues — see `journalctl -u usa-wa-sync-powermap`.\n\n"
            f"--- last cycle's component errors ---\n{errors}\n"
        )
        try:
            await self._alert(subject, body)
        except Exception:
            logger.exception("sidecar_streak_alert_failed", extra={"streak": streak})

    async def _subscription_backstop_due(self, session: AsyncSession, now: datetime) -> bool:
        """Whether the in-loop re-discovery backstop should run this cycle.

        Due immediately on first run (no stamp), then every
        ``subscription_backstop_cadence``. Mirrors :meth:`_reconcile_due`, keyed on
        the ``subscriptions`` stream's ``last_reconcile_at``.
        """
        state = (
            await session.execute(select(SyncState).where(SyncState.stream == SUBSCRIPTIONS_STREAM))
        ).scalar_one_or_none()
        if state is None or state.last_reconcile_at is None:
            return True
        return (now - state.last_reconcile_at) >= self._subscription_backstop_cadence

    async def _mark_subscription_synced(self, session: AsyncSession, now: datetime) -> None:
        """Stamp the ``subscriptions`` stream so the backstop waits a full cadence."""
        state = (
            await session.execute(select(SyncState).where(SyncState.stream == SUBSCRIPTIONS_STREAM))
        ).scalar_one_or_none()
        if state is None:
            state = SyncState(stream=SUBSCRIPTIONS_STREAM)
            session.add(state)
        state.last_reconcile_at = now
        await session.flush()

    async def _reconcile_due(
        self, session: AsyncSession, descriptor: EntityDescriptor, now: datetime
    ) -> bool:
        # Gate per reconcile_mode (usa-wa#13): ``none`` runs no backstop and is always
        # skipped (jurisdictions — driven by the subscription feed + discovery). The
        # ``full_list`` (sibling-only) and ``anchored_cohort`` (cohort producers)
        # backstops both run on cadence; engine.reconcile() dispatches the right one.
        if descriptor.read_source == "none" or descriptor.reconcile_mode == "none":
            return False
        stream = f"reconcile:{descriptor.entity_type}"
        state = (
            await session.execute(select(SyncState).where(SyncState.stream == stream))
        ).scalar_one_or_none()
        if state is None or state.last_reconcile_at is None:
            return True
        if state.cursor is not None:
            # An interrupted pass left a keyset checkpoint (#94) — resume it now regardless
            # of the cadence, so a reconcile broken off mid-cohort finishes promptly.
            return True
        return (now - state.last_reconcile_at) >= descriptor.reconcile_cadence
