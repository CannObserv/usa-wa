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

Commit boundary: per-cycle commit today. Per-entry commit with a configurable
chunk size is the refinement tracked in CannObserv/usa-wa#8.
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
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._engine = engine
        self._descriptors = list(descriptors)
        self._session_factory = session_factory
        self._feed_poll_seconds = feed_poll_seconds
        self._reconciler = reconciler
        self._subscription_backstop_cadence = subscription_backstop_cadence
        self._clock = clock

    async def tick(self, session: AsyncSession, *, now: datetime) -> None:
        """One sync cycle against a single session (no commit — caller owns it)."""
        # Membership first: re-discover the WA subtree and register/backfill any new
        # entities BEFORE the feed pull, so their changes are delivered this cycle.
        # Failure here is contained per-cycle by run_cycle (rollback + log); the feed
        # via already-registered subscriptions still works next cycle.
        if self._reconciler is not None and await self._subscription_backstop_due(session, now):
            await self._reconciler.sync_subscriptions(session)
            await self._mark_subscription_synced(session, now)
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
        await self._engine.drain_outbox(session, now=now)

    async def run_cycle(self) -> None:
        """Run one isolated cycle: own session, commit on success, rollback on error."""
        now = self._clock()
        async with self._session_factory() as session:
            try:
                await self.tick(session, now=now)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("sidecar_cycle_failed")

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
