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
from clearinghouse_sync_powermap.engine import SyncEngine, outbox_backlog, rejected_breakdown
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
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
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

    async def run_cycle(self) -> bool:
        """Run one isolated cycle; return the cycle verdict (True = fully clean).

        Component order: catalog sync → re-discovery backstop → per-descriptor
        reconciles → main tick (feed → sweep → drain). Each component runs in its
        own session + error boundary, so any one failing leaves the rest running.
        Containment must not defeat the retry-pressure signal (#85): any contained
        component failure flips the verdict to False, which :meth:`run_forever`
        turns into exponential backoff + streak alerting.
        """
        now = self._clock()
        self._cycle_errors = []
        ok = await self._run_catalog_sync(now)
        ok = await self._run_backstop(now) and ok
        ok = await self._run_reconciles(now) and ok
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
        logger.info(
            "sidecar_cycle_summary",
            extra={
                "pending": backlog.pending,
                "pending_due": backlog.pending_due,
                "rejected": backlog.rejected,
                "unavailable": backlog.unavailable,
                "oldest_pending_age_seconds": backlog.oldest_pending_age_seconds,
                "rejected_reasons": reasons,
            },
        )
        if backlog.rejected > self._last_rejected_count:
            await self._send_rejected_alert(backlog.rejected, reasons)
        self._last_rejected_count = backlog.rejected

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
        return (now - state.last_reconcile_at) >= descriptor.reconcile_cadence
