"""EngineContext — the immutable floor the three sync managers stand on (#181).

The :class:`~clearinghouse_sync_powermap.engine.SyncEngine` split needs somewhere
for the state its three managers genuinely *share* to live, or the managers import
each other and the package cycles. Everything here is either immutable (the
descriptor registry, the PM client, the tunables) or a pure function, so this module
sits at the bottom of the package's dependency DAG and imports nothing from above:

    context  ←  anchors  ←  write  ←  read  ←  __init__ (the SyncEngine façade)

**Not a god object.** The context deliberately holds *no mutable engine state*. Each
per-process counter and warn-once throttle has exactly one owner among the managers
(``_warned_stuck`` and the drain tallies belong to :class:`~clearinghouse_sync_powermap
.engine.write.OutboxWriter`, ``_warned_nonconverging`` to
:class:`~clearinghouse_sync_powermap.engine.anchors.AnchorManager`, ``_warned_dead_anchors``
and the conditional-GET tallies to :class:`~clearinghouse_sync_powermap.engine.read.Reconciler`),
so no manager can reach through the context to mutate another's bookkeeping. The
``AsyncSession`` is *not* held here either — every engine method still takes an explicit
``session`` (and an explicit ``now`` where a clock matters), as it always has.

What the context does own beyond configuration is the PM **read** pause-and-resume
(:meth:`EngineContext.read_with_retry` and its two descriptor wrappers). That helper is
the one thing both the write path (the sweep's PM-first match) and the read path (the
reconcile crawl, the feed, the replay) need; leaving it on either manager would make
``write`` and ``read`` mutually dependent. It is transport policy, not sync logic, so
the floor is its natural home — as is :func:`enrich_fingerprint`, the content hash three
different ledgers above key on (the outbox ``payload_hash``, ``EnrichFingerprint``, and
``NonConvergenceState``).
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta
from typing import Any

import httpx

from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.client import (
    EntityFetch,
    PowerMapClient,
    RetryableClientError,
)
from clearinghouse_sync_powermap.descriptors import EntityDescriptor

logger = get_logger(__name__)


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

#: Default replay margin in outbox-seq units (usa-wa#159): each replay cycle re-reads
#: the changes feed from ``high_water − margin`` so a concurrent-commit-skipped seq
#: (a lower seq that committed *after* the live consumer advanced past it — the
#: power-map#387 at-least-once hazard) is re-delivered and re-applied under LWW. The
#: margin must exceed PM's worst-case in-flight-write / bulk-import span (the largest
#: gap between an assigned seq and its commit); 10_000 is generous for a low-churn
#: dataset — the feed is subscription-filtered, so a wide raw-seq window still yields
#: only our few items and stays cheap. Env-tunable via ``SidecarSettings.replay_margin``.
DEFAULT_REPLAY_MARGIN = 10_000

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

#: Page size for :meth:`~clearinghouse_sync_powermap.engine.write.OutboxWriter.sweep_unanchored`
#: (#7). The sweep keyset-pages the unanchored rows by primary key instead of materialising
#: them all at once, so a first bulk identity ingest (persons/orgs in the thousands) never
#: loads the whole backlog into memory per cycle. Jurisdictions (~100 rows) fit in one page.
#: The anchored-cohort reconcile crawl reuses it as its own page size.
DEFAULT_SWEEP_BATCH_SIZE = 500

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


class EngineContext:
    """Immutable shared state + PM read transport for the three sync managers.

    Constructed once by :class:`~clearinghouse_sync_powermap.engine.SyncEngine` and
    constructor-injected into :class:`~clearinghouse_sync_powermap.engine.anchors.AnchorManager`,
    :class:`~clearinghouse_sync_powermap.engine.write.OutboxWriter` and
    :class:`~clearinghouse_sync_powermap.engine.read.Reconciler`. Injection (rather than
    handing each manager the façade) is what keeps the façade thin: a manager can only
    reach the descriptor registry, the client, and the tunables — never another manager's
    methods or state by accident.

    Argument validation lives here rather than on the façade so the invariants travel
    with the values they constrain; ``SyncEngine(...)`` still raises the same
    ``ValueError``s from its constructor because it builds the context eagerly.
    """

    def __init__(
        self,
        descriptors: Sequence[EntityDescriptor],
        client: PowerMapClient,
        *,
        batch_limit: int,
        max_attempts: int,
        deferred_stuck_threshold: timedelta,
        sweep_batch_size: int,
        nonconvergence_threshold: int,
        replay_margin: int,
        conditional_get_enabled: bool,
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        if sweep_batch_size < 1:
            raise ValueError("sweep_batch_size must be >= 1")
        if nonconvergence_threshold < 1:
            # A 0/negative threshold flags on the first stable re-observe AND makes
            # ``nonconverging_count``'s ``count >= threshold`` match every *reset* (count 0)
            # row — inverting the standing query into "the whole cohort" and turning the
            # rise-alert into a per-cycle flood naming converged rows (#112 CR-1).
            raise ValueError("nonconvergence_threshold must be >= 1")
        if replay_margin < 0:
            # A negative margin would push the replay floor *above* high_water,
            # skipping the very skip-window it exists to re-cover (usa-wa#159). 0 is
            # legal — it re-reads nothing below high_water (replay effectively off).
            raise ValueError("replay_margin must be >= 0")
        self.by_type = {d.entity_type: d for d in descriptors}
        #: Drain priority per entity type = its index in the (dependency-first)
        #: descriptor registry order. Lower drains first, so a dependency **root**
        #: (org/role) is always attempted before its dependents (assignments) in a
        #: single batch — the fix for the #96 bulk-produce starvation, where frozen
        #: role roots were crowded out of the ``next_attempt_at``-only ``LIMIT`` cut
        #: by thousands of dependency-blocked assignments deferred just ahead of them.
        #: ``build_descriptors`` authors this order; it is load-bearing here, not
        #: merely informational.
        self.drain_priority = {d.entity_type: i for i, d in enumerate(descriptors)}
        self.client = client
        self.batch_limit = batch_limit
        self.max_attempts = max_attempts
        self.deferred_stuck_threshold = deferred_stuck_threshold
        self.sweep_batch_size = sweep_batch_size
        self.nonconvergence_threshold = nonconvergence_threshold
        self.replay_margin = replay_margin
        self.conditional_get_enabled = conditional_get_enabled
        # Injectable for the transient-read retry tests (usa-wa#85); production sleeps.
        self.sleep = sleep

    def descriptor_for(self, entity_type: str) -> EntityDescriptor | None:
        return self.by_type.get(entity_type)

    @property
    def descriptors(self) -> tuple[EntityDescriptor, ...]:
        """All registered descriptors (read-only). Lets membership managers (e.g. the
        subscription reconciler's local-cohort discovery) enumerate the entity set."""
        return tuple(self.by_type.values())

    # --- PM read transport: pause-and-resume (usa-wa#85) ----------------------

    async def read_with_retry(
        self, make_awaitable: Callable[[], Awaitable[Any]], *, log_extra: dict
    ) -> Any:
        """Run a PM *read* with a bounded pause-and-resume on 429/5xx.

        usa-wa#85/#89: PM's rate limit is live, and a read burst is exactly what trips
        it. A :class:`RetryableClientError` sleeps the server's ``Retry-After`` hint
        (else the :data:`READ_BACKOFF_SECONDS` step) and retries in place, so the read
        resumes instead of aborting the cycle — which would leave the cadence unstamped,
        re-crawl from the top next cycle, and re-trip the limiter. Shared by every read
        whose bare 429 was cycle-fatal: the anchored-cohort reconcile (``read`` =
        ``reconcile``), the sweep's PM-first match (``sweep_match``), the subscription
        backfill (``backfill``), and the changes-feed read (``feed``). A failure
        outlasting the budget re-raises into the caller's error boundary (the sidecar's
        per-component containment)."""
        for delay in READ_BACKOFF_SECONDS:
            try:
                return await make_awaitable()
            except RetryableClientError as exc:
                wait = exc.retry_after if exc.retry_after is not None else delay
                logger.warning(
                    "read_backoff",
                    extra={**log_extra, "wait_seconds": wait, "error": str(exc)},
                )
                await self.sleep(wait)
        return await make_awaitable()

    async def fetch_record_with_retry(self, descriptor: EntityDescriptor, pm_id: Any) -> Any:
        """``descriptor.fetch_record`` with the shared read pause-and-resume (#85)."""
        return await self.read_with_retry(
            lambda: descriptor.fetch_record(self.client, pm_id),
            log_extra={
                "read": "reconcile",
                "entity_type": descriptor.entity_type,
                "pm_id": str(pm_id),
            },
        )

    async def fetch_record_conditional_with_retry(
        self, descriptor: EntityDescriptor, pm_id: Any, if_none_match: str | None
    ) -> EntityFetch:
        """``descriptor.fetch_record_conditional`` with the shared read pause-and-resume (#85)."""
        return await self.read_with_retry(
            lambda: descriptor.fetch_record_conditional(
                self.client, pm_id, if_none_match=if_none_match
            ),
            log_extra={
                "read": "reconcile",
                "entity_type": descriptor.entity_type,
                "pm_id": str(pm_id),
                "conditional": True,
            },
        )

    async def fetch_record_for_backfill(self, descriptor: EntityDescriptor, pm_id: Any) -> Any:
        """The subscription backfill's fetch (usa-wa#89): a newly-subscribed entity's
        current state with the same 429 pause-and-resume the reconcile crawl uses, so a
        rate-limit mid-backfill doesn't abort the backstop before it stamps (→ re-crawl →
        re-trip). Distinct from :meth:`fetch_record_with_retry` only in its ``read`` log
        tag, which is how an operator tells the two read sources apart in journald."""
        return await self.read_with_retry(
            lambda: descriptor.fetch_record(self.client, pm_id),
            log_extra={
                "read": "backfill",
                "entity_type": descriptor.entity_type,
                "pm_id": str(pm_id),
            },
        )
