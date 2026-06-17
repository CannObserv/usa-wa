"""The long-running sidecar — drives the engine one cycle at a time.

Process model B (single daemon). Each cycle: run the due subscription re-discovery
backstop (register/backfill new WA-subtree entities), pull the subscription-filtered
changes feed, sweep un-anchored rows, and drain the outbox. The legacy full-list
reconcile is retired for usa-wa (all descriptors opt out) but the generic hook
remains for siblings.

Per-cycle isolation (CR #13): every cycle runs in its own session inside a
try/except that rolls back and logs on failure, so a propagating non-transient
error (the outbox worker no longer swallows bugs as transient) cannot kill the
daemon or poison the next cycle.

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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.engine import SyncEngine
from clearinghouse_sync_powermap.models import SyncState
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
        subscription_backstop_cadence: timedelta = timedelta(hours=1),
        outbox_commit_chunk_size: int = 1,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._engine = engine
        self._descriptors = list(descriptors)
        self._session_factory = session_factory
        self._feed_poll_seconds = feed_poll_seconds
        self._reconciler = reconciler
        self._subscription_backstop_cadence = subscription_backstop_cadence
        self._outbox_commit_chunk_size = outbox_commit_chunk_size
        self._clock = clock

    async def tick(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """One sync cycle against a single session.

        Reads (feed + due reconcile) and the sweep enqueue accumulate in the open
        transaction; the caller owns their commit. When ``commit`` is supplied, the
        outbox *drain* commits incrementally — per delivered entry by default, or
        every ``outbox_commit_chunk_size`` entries (#8) — so a slow PM never holds
        the transaction open across every delivery round-trip. With no ``commit``
        hook the whole tick is one transaction (the legacy boundary).

        The subscription re-discovery backstop is NOT run here — it runs in its own
        session via :meth:`run_cycle` so a discovery/PM failure cannot roll back or
        starve the feed/sweep/drain in this transaction.
        """
        # Reads: incremental feed first, then due reconcile backstops (retired for
        # usa-wa — all descriptors opt out — but kept generic for siblings).
        await self._engine.process_feed(session)
        for descriptor in self._descriptors:
            if await self._reconcile_due(session, descriptor, now):
                await self._engine.reconcile(session, descriptor, now=now)
        # Writes: enqueue un-anchored rows, then deliver.
        for descriptor in self._descriptors:
            if descriptor.write_enabled:
                await self._engine.sweep_unanchored(session, descriptor)
        await self._engine.drain_outbox(
            session, now=now, commit=commit, chunk_size=self._outbox_commit_chunk_size
        )

    async def run_cycle(self) -> None:
        """Run one isolated cycle: own session, commit on success, rollback on error.

        The re-discovery backstop runs first in its OWN session (:meth:`_run_backstop`),
        so a discovery/PM failure is contained there and the main tick (feed → sweep →
        drain) still runs against already-registered subscriptions.
        """
        now = self._clock()
        await self._run_backstop(now)
        async with self._session_factory() as session:
            try:
                # The drain commits incrementally via this hook (#8); the trailing
                # commit covers the read/sweep work and any sub-chunk drain remainder.
                await self.tick(session, now=now, commit=session.commit)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("sidecar_cycle_failed")

    async def _run_backstop(self, now: datetime) -> None:
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
        """
        if self._reconciler is None:
            return
        try:
            async with self._session_factory() as session:
                if await self.run_subscription_backstop(session, now=now):
                    await session.commit()
        except Exception:
            logger.exception("subscription_backstop_failed")

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
        """Loop cycles forever, sleeping between them."""
        logger.info(
            "sidecar_started",
            extra={"entities": [d.entity_type for d in self._descriptors]},
        )
        while True:
            await self.run_cycle()
            await sleep(self._feed_poll_seconds)

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
        # Only full-mirror entities run the full-list reconcile backstop; cohort-only
        # producers opt out (would page PM's entire set to discard it). See usa-wa#13.
        if descriptor.read_source == "none" or not descriptor.reconcile_enabled:
            return False
        stream = f"reconcile:{descriptor.entity_type}"
        state = (
            await session.execute(select(SyncState).where(SyncState.stream == stream))
        ).scalar_one_or_none()
        if state is None or state.last_reconcile_at is None:
            return True
        return (now - state.last_reconcile_at) >= descriptor.reconcile_cadence
