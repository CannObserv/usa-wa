"""Subscription reconciler — aligns the PM subscription set to a discovery spec.

Separate from :class:`SyncEngine`: membership management (which entities this key
follows) is a distinct concern from per-row sync. Under PM #203 the changes feed is
subscription-filtered, so the sidecar must register the WA-relevant entities before
the feed will deliver anything for them.

Additive-only by design (see CannObserv/usa-wa#10): :meth:`sync_subscriptions`
discovers the current WA subtree, registers the entities not already subscribed, and
backfills their current state by id (the feed is forward-only, so new subscriptions
see only future changes). It never unsubscribes and never evicts cache rows — pruning
is deferred. Bootstrap and the periodic backstop are the *same* call: bootstrap is
just the first run, when the registered set is empty and everything is backfilled.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.client import PowerMapClient
from clearinghouse_sync_powermap.engine import SyncEngine

logger = get_logger(__name__)


@dataclass(frozen=True)
class DiscoverySpec:
    """Where discovery starts and which edges it follows (deployment-specific).

    ``follow`` is the ordered set of PM edge types to traverse — for usa-wa:
    ``lineage``, ``affiliated_orgs``, ``org_children``, ``roles``, ``assignments``,
    ``people`` rooted at the ``usa-wa`` jurisdiction.
    """

    root_type: str
    root_id: str
    follow: Sequence[str]


@dataclass(frozen=True)
class SubscriptionSyncReport:
    """Observability counts for one :meth:`SubscriptionReconciler.sync_subscriptions`.

    ``discovered`` is the full candidate set; ``newly_subscribed`` the additive diff
    just registered; ``backfilled`` the new rows whose current state was fetched and
    applied; ``not_found`` the ids PM could not resolve; ``skipped_unknown_type`` the
    discovered candidates with no local descriptor.
    """

    discovered: int
    already_registered: int
    newly_subscribed: int
    backfilled: int
    not_found: int
    skipped_unknown_type: int


class SubscriptionReconciler:
    """Keeps the PM subscription set aligned to a :class:`DiscoverySpec` (additive)."""

    def __init__(self, client: PowerMapClient, engine: SyncEngine, spec: DiscoverySpec) -> None:
        self._client = client
        self._engine = engine
        self._spec = spec

    async def sync_subscriptions(self, session: AsyncSession) -> SubscriptionSyncReport:
        """Discover → register new → backfill new. Additive-only; idempotent.

        A re-run with no graph drift discovers the same set, finds it all already
        registered, registers nothing, and backfills nothing — a no-op.
        """
        discovered = await self._client.discover(
            root_type=self._spec.root_type,
            root_id=self._spec.root_id,
            follow=self._spec.follow,
        )
        registered = set(await self._client.list_subscriptions())
        new = [d for d in discovered if d.entity_id not in registered]

        not_found: set = set()
        if new:
            result = await self._client.add_subscriptions([d.entity_id for d in new])
            not_found = set(result.not_found)
            if result.not_found:
                logger.warning(
                    "subscription_register_not_found",
                    extra={"count": len(result.not_found)},
                )

        backfilled = 0
        skipped_unknown = 0
        for d in new:
            if d.entity_id in not_found:
                continue
            descriptor = self._engine.descriptor_for(d.entity_type)
            if descriptor is None:
                # Discovery surfaced a type this deployment does not model — register
                # it (done above) but skip backfill; the feed will also skip it.
                skipped_unknown += 1
                logger.warning(
                    "subscription_unknown_entity_type",
                    extra={"entity_type": d.entity_type, "entity_id": str(d.entity_id)},
                )
                continue
            record = await descriptor.fetch_record(self._client, d.entity_id)
            if record is None:
                # Subscribed but the entity could not be fetched (e.g. 404 between
                # discovery and backfill); the feed will deliver it if it reappears.
                continue
            await self._engine.apply_record(session, descriptor, record)
            backfilled += 1

        await session.flush()
        report = SubscriptionSyncReport(
            discovered=len(discovered),
            already_registered=len(discovered) - len(new),
            newly_subscribed=len(new) - len(not_found),
            backfilled=backfilled,
            not_found=len(not_found),
            skipped_unknown_type=skipped_unknown,
        )
        logger.info(
            "subscription_sync",
            extra={
                "discovered": report.discovered,
                "newly_subscribed": report.newly_subscribed,
                "backfilled": report.backfilled,
                "not_found": report.not_found,
                "skipped_unknown_type": report.skipped_unknown_type,
            },
        )
        return report
