"""Subscription reconciler — aligns the PM subscription set to a discovery spec.

Separate from :class:`SyncEngine`: membership management (which entities this key
follows) is a distinct concern from per-row sync. Under PM #203 the changes feed is
subscription-filtered, so the sidecar must register the WA-relevant entities before
the feed will deliver anything for them.

Additive by design (see CannObserv/usa-wa#10): :meth:`sync_subscriptions` discovers the
desired mirror set (PM discovery ∪ the local anchored cohort — see
:meth:`SubscriptionReconciler._discover_mirror_set`), registers the entities not already
subscribed, and backfills their current state by id (the feed is forward-only, so new
subscriptions see only future changes). It never unsubscribes and never evicts cache
rows. Bootstrap and the periodic backstop are the *same* call: bootstrap is just the
first run, when the registered set is empty and everything is backfilled.

Unsubscribing is a *separate*, deliberate path: :meth:`prune_subscriptions` (#73 Axis 1)
diffs PM's registered set against the freshly-discovered mirror set and removes the
difference under guardrails — the guarded reclaim for strangers a prior, broader
discovery scope left subscribed. It still never evicts a local cache row.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.client import DiscoveredEntity, PowerMapClient
from clearinghouse_sync_powermap.engine import APPLY_INSERTED, APPLY_UPDATED, SyncEngine

logger = get_logger(__name__)

#: Keyset page size for the local anchored-cohort enumeration (#73 Axis 1) — mirrors
#: the engine's reconcile paging so a large cohort never materialises all at once.
_LOCAL_COHORT_PAGE_SIZE = 500

#: Default prune floor (#73 Axis 1 step 6): abort a prune whose stale fraction of the
#: registered set exceeds this — a near-total wipe signals a discovery collapse, not a
#: real cleanup. Deliberately permissive (0.9): the FIRST prune legitimately removes the
#: large stranger backlog (~half the set), so only a wipe-almost-everything run aborts.
DEFAULT_MAX_PRUNE_FRACTION = 0.9


@dataclass(frozen=True)
class DiscoverySpec:
    """Where discovery starts and which edges it follows (deployment-specific).

    ``follow`` is the ordered set of PM edge types to traverse from ``root_id`` (e.g.
    ``lineage``, ``org_children``, ``roles``). A deployment that subscribes its produced
    rows from the local cohort (:attr:`SubscriptionReconciler.include_local_cohort`)
    narrows this to just the mirror-only edges it does not produce.
    """

    root_type: str
    root_id: str
    follow: Sequence[str]


@dataclass(frozen=True)
class SubscriptionSyncReport:
    """Observability counts for one :meth:`SubscriptionReconciler.sync_subscriptions`.

    ``discovered`` is the full candidate set; ``newly_subscribed`` the count PM
    reports as *genuinely* newly registered (``add_subscriptions().registered``, not
    ``len(new)``): under PM's under-reporting subscription pagination (power-map#297)
    the candidate diff resurfaces already-subscribed ids every cycle, which PM returns
    as ``already_subscribed`` — so sourcing this from PM keeps the metric honest rather
    than pinned at the phantom-diff size; ``backfilled`` the new rows actually written
    to the cache
    (``apply_record`` returned inserted/updated); ``backfill_skipped`` the new rows an
    update-only producer descriptor declined to mirror (a record usa-wa never produced)
    or that could not be fetched; ``already_cached`` the new rows skipped because we
    already hold them locally (the phantom-new crawl PM's under-reporting subscription
    pagination resurfaces — usa-wa#89); ``not_found`` the ids PM could not resolve;
    ``skipped_unknown_type`` the discovered candidates with no local descriptor.
    """

    discovered: int
    already_registered: int
    newly_subscribed: int
    backfilled: int
    backfill_skipped: int
    already_cached: int
    not_found: int
    skipped_unknown_type: int


class SubscriptionReconciler:
    """Keeps the PM subscription set aligned to a :class:`DiscoverySpec` (additive).

    ``include_local_cohort`` (#73 Axis 1): when True, the candidate set is augmented
    with OUR locally-anchored producer rows (see :meth:`_discover_local_cohort`), so the
    feed is subscribed to PM's edits of entities usa-wa *produced* without following the
    PM subtree into PM-only rows we never mirror (the ~1,000-stranger over-subscription).
    Default False keeps the portable, PM-subtree-only behaviour for sibling deployments.
    """

    def __init__(
        self,
        client: PowerMapClient,
        engine: SyncEngine,
        spec: DiscoverySpec,
        *,
        include_local_cohort: bool = False,
    ) -> None:
        self._client = client
        self._engine = engine
        self._spec = spec
        self.include_local_cohort = include_local_cohort

    async def sync_subscriptions(self, session: AsyncSession) -> SubscriptionSyncReport:
        """Discover → register new → backfill new. Additive-only; idempotent.

        A re-run with no graph drift discovers the same set, finds it all already
        registered, registers nothing, and backfills nothing — a no-op.
        """
        discovered = await self._discover_mirror_set(session)
        registered = set(await self._client.list_subscriptions())
        new = [d for d in discovered if d.entity_id not in registered]

        not_found: set = set()
        newly_registered = 0
        if new:
            result = await self._client.add_subscriptions([d.entity_id for d in new])
            not_found = set(result.not_found)
            # PM's authoritative count of ids it actually registered (vs the ones it
            # returns as already_subscribed). len(new) over-reports here because PM's
            # /subscriptions pagination under-reports the registered set (power-map#297),
            # so the diff perpetually re-includes already-subscribed ids.
            newly_registered = result.registered
            if result.not_found:
                logger.warning(
                    "subscription_register_not_found",
                    extra={"count": len(result.not_found)},
                )

        backfilled = 0
        backfill_skipped = 0
        already_cached = 0
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
            # Skip the backfill for an entity we already hold locally (usa-wa#89). The
            # backfill exists only to seed a newly-subscribed entity the forward-only
            # feed won't retroactively deliver; a row we already anchored is kept current
            # by the feed + reconcile backstop. This collapses the phantom-new crawl:
            # PM's /subscriptions pagination can under-report the registered set
            # (power-map#297), so already-subscribed rows resurface as `new` every cycle
            # — re-fetching each was the burst that tripped PM's 429. add_subscriptions
            # above is idempotent, so re-registering them is a harmless no-op.
            # has_local_anchor matches ANY anchored row, incl. a tombstoned/archived one:
            # a locally-deleted row is intentionally NOT resurrected by a backfill — the
            # feed's explicit delete/merge events drive its lifecycle, and the reconcile
            # backstop already excludes tombstoned rows from its re-fetch cohort.
            if await self._engine.has_local_anchor(session, descriptor, d.entity_id):
                already_cached += 1
                continue
            record = await self._engine.fetch_record_with_retry(descriptor, d.entity_id)
            if record is None:
                # Subscribed but the entity could not be fetched (e.g. 404 between
                # discovery and backfill); the feed will deliver it if it reappears.
                backfill_skipped += 1
                continue
            outcome = await self._engine.apply_record(session, descriptor, record)
            # Update-only producer descriptors decline to mirror a record usa-wa never
            # produced (APPLY_SKIPPED) and LWW may keep the local row (APPLY_KEPT_LOCAL);
            # only count a real cache write as a backfill so the log is not inflated.
            if outcome in (APPLY_INSERTED, APPLY_UPDATED):
                backfilled += 1
            else:
                backfill_skipped += 1

        await session.flush()
        report = SubscriptionSyncReport(
            discovered=len(discovered),
            already_registered=len(discovered) - len(new),
            newly_subscribed=newly_registered,
            backfilled=backfilled,
            backfill_skipped=backfill_skipped,
            already_cached=already_cached,
            not_found=len(not_found),
            skipped_unknown_type=skipped_unknown,
        )
        logger.info(
            "subscription_sync",
            extra={
                "discovered": report.discovered,
                "newly_subscribed": report.newly_subscribed,
                "backfilled": report.backfilled,
                "backfill_skipped": report.backfill_skipped,
                "already_cached": report.already_cached,
                "not_found": report.not_found,
                "skipped_unknown_type": report.skipped_unknown_type,
            },
        )
        return report

    async def prune_subscriptions(
        self,
        session: AsyncSession,
        *,
        max_prune_fraction: float = DEFAULT_MAX_PRUNE_FRACTION,
        dry_run: bool = False,
    ) -> dict:
        """Unsubscribe entities no longer in the desired mirror set (#73 Axis 1 step 6).

        The additive :meth:`sync_subscriptions` never removes, so narrowing the discovery
        scope leaves the previously-subscribed strangers registered-but-inert (the feed
        still delivers + the reconciler still skips them). This is the deliberate,
        guarded reclaim: diff PM's registered set against the freshly-discovered mirror
        set and :meth:`PowerMapClient.remove_subscriptions` the difference.

        Guardrails against a discovery collapse mass-unsubscribing everything: an **empty**
        desired set aborts (``empty_desired``); a stale fraction over ``max_prune_fraction``
        aborts (``prune_floor``). ``dry_run`` computes the diff + guards but removes nothing.
        Idempotent — a re-run once aligned finds nothing stale. Returns a JSON-able summary.
        """
        desired = await self._discover_mirror_set(session)
        desired_ids = {d.entity_id for d in desired}
        registered = list(await self._client.list_subscriptions())
        stale = [entity_id for entity_id in registered if entity_id not in desired_ids]
        summary = {
            "registered": len(registered),
            "desired": len(desired_ids),
            "stale": len(stale),
            "removed": 0,
            "dry_run": dry_run,
            "aborted": None,
        }
        if not desired_ids:
            # A discovery failure would make every subscription look stale — never nuke
            # the whole set on an empty desired set.
            summary["aborted"] = "empty_desired"
            logger.warning("subscription_prune_aborted", extra={"reason": "empty_desired"})
            return summary
        if registered and len(stale) / len(registered) > max_prune_fraction:
            summary["aborted"] = "prune_floor"
            logger.warning(
                "subscription_prune_aborted",
                extra={
                    "reason": "prune_floor",
                    "stale": len(stale),
                    "registered": len(registered),
                    "max_prune_fraction": max_prune_fraction,
                },
            )
            return summary
        if dry_run or not stale:
            return summary
        removed = await self._client.remove_subscriptions(stale)
        summary["removed"] = removed
        logger.info("subscription_prune", extra={"removed": removed, "stale": len(stale)})
        if removed < len(stale):
            # A bulk remove that dropped fewer than requested — a partial PM-side failure.
            # Surface it (the summary carries both counts); a re-run retries the remainder.
            logger.warning(
                "subscription_prune_partial",
                extra={"removed": removed, "stale": len(stale)},
            )
        return summary

    async def _discover_mirror_set(self, session: AsyncSession) -> list[DiscoveredEntity]:
        """The desired subscription set: PM discovery ∪ the local cohort, deduped.

        Shared by :meth:`sync_subscriptions` (what to register) and
        :meth:`prune_subscriptions` (what to keep). ``include_local_cohort`` gates whether
        our produced rows are folded in (see the class docstring).
        """
        candidates = list(
            await self._client.discover(
                root_type=self._spec.root_type,
                root_id=self._spec.root_id,
                follow=self._spec.follow,
            )
        )
        if self.include_local_cohort:
            candidates.extend(await self._discover_local_cohort(session))
        return _dedupe_by_entity_id(candidates)

    async def _discover_local_cohort(self, session: AsyncSession) -> list[DiscoveredEntity]:
        """Enumerate OUR anchored rows as subscription candidates (#73 Axis 1).

        For each descriptor that runs a reconcile backstop (``reconcile_enabled`` — the
        producer entities whose anchored rows usa-wa maintains; PM-authoritative types
        like jurisdictions have no backstop and are excluded, staying PM-discovered),
        keyset-page the rows carrying a live anchor and emit each as a
        :class:`DiscoveredEntity` keyed on its PM anchor id. This subscribes the feed to
        PM's edits of rows WE produced without walking the PM subtree into strangers we
        never mirror.

        Tombstoned rows (``deleted_column`` set) are skipped — never re-subscribed —
        mirroring the anchored-cohort reconcile. An *archived* row keeps a live anchor
        and IS included, so a dropped un-archive event still self-heals via the feed.
        """
        discovered: list[DiscoveredEntity] = []
        for descriptor in self._engine.descriptors:
            if not descriptor.reconcile_enabled:
                continue
            anchor_col = descriptor.anchor_column_expr()
            pk_col = descriptor.model.id
            last_id = None
            while True:
                stmt = select(descriptor.model).where(anchor_col.is_not(None))
                if descriptor.deleted_column is not None:
                    stmt = stmt.where(descriptor.deleted_column_expr().is_(None))
                if last_id is not None:
                    stmt = stmt.where(pk_col > last_id)
                stmt = stmt.order_by(pk_col).limit(_LOCAL_COHORT_PAGE_SIZE)
                rows = (await session.execute(stmt)).scalars().all()
                if not rows:
                    break
                for row in rows:
                    last_id = row.id
                    discovered.append(
                        DiscoveredEntity(
                            entity_type=descriptor.entity_type,
                            entity_id=descriptor.anchor_value(row),
                            display_name=None,
                            hops_from_root=0,
                        )
                    )
                if len(rows) < _LOCAL_COHORT_PAGE_SIZE:
                    break
        return discovered


def _dedupe_by_entity_id(candidates: list[DiscoveredEntity]) -> list[DiscoveredEntity]:
    """Order-preserving dedup by ``entity_id`` (first occurrence wins).

    PM discovery and the local cohort can surface the same id (an org we produced is
    both in the WA subtree and our anchored cohort); registering it once keeps the
    additive diff honest and ``add_subscriptions`` idempotent.
    """
    seen: set = set()
    unique: list[DiscoveredEntity] = []
    for candidate in candidates:
        if candidate.entity_id in seen:
            continue
        seen.add(candidate.entity_id)
        unique.append(candidate)
    return unique
