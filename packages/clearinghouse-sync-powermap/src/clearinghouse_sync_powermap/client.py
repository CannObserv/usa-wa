"""Power Map client protocol + value types.

The engine depends only on :class:`PowerMapClient` (a Protocol), never on a
concrete HTTP implementation. Step 5 adds the real client (a wrapper over the
``openapi-python-client``-generated SDK); tests supply a fake that satisfies the
same Protocol. Keeping the seam here is what lets the engine stay portable and
unit-testable without a live PM.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ulid import ULID

from clearinghouse_sync_powermap.models import (
    DISPOSITION_AUTO_ATTACHED,
    DISPOSITION_NEW,
    DISPOSITION_REJECTED,
)

#: Dispositions that successfully anchor a local row to a PM id.
ANCHORING_DISPOSITIONS = frozenset({DISPOSITION_AUTO_ATTACHED, DISPOSITION_NEW})


class RetryableClientError(Exception):
    """A transient client/transport failure the engine should back off and retry.

    Defined here (portable, PM-agnostic) so the engine's transient-exception set
    stays free of any concrete-client imports. A concrete client raises this for
    failures worth retrying (e.g. a PM 5xx / 429) instead of leaking SDK-specific
    exception types into the generic engine.
    """


class DeliveryBlockedError(Exception):
    """A permanent transport/auth rejection that no retry will clear without
    operator intervention (e.g. a PM ``403 Insufficient scope`` or ``401``).

    Portable counterpart to :class:`RetryableClientError`: a concrete client
    raises this for non-retryable auth/permission statuses so the engine can park
    the entry to ``UNAVAILABLE`` (re-drivable once the credential is fixed) instead
    of letting an SDK exception escape and roll back the whole sync cycle. Recovery
    is operator-driven: fix the key/scope, then ``redrive_unavailable``.
    """


class PayloadRejectedError(Exception):
    """PM permanently refused the request payload (e.g. a ``422`` schema-validation
    failure, or another non-auth ``4xx``).

    Distinct from :class:`DeliveryBlockedError`: the fix is to the data/payload, not
    the credential, so the engine parks the entry to ``REJECTED`` — the re-sweepable
    terminal state — rather than re-attempting a request that will never validate.
    """


@dataclass(frozen=True)
class ObservationResult:
    """Outcome of a single ``POST .../observations`` call."""

    disposition: str
    pm_id: ULID | None
    raw: dict

    @property
    def anchored(self) -> bool:
        """True when PM accepted the observation and returned a usable id."""
        return self.disposition in ANCHORING_DISPOSITIONS and self.pm_id is not None

    @property
    def rejected(self) -> bool:
        return self.disposition == DISPOSITION_REJECTED


@dataclass(frozen=True)
class ChangeItem:
    """One entry from ``GET /api/v1/changes``."""

    entity_type: str
    entity_id: ULID
    changed_at: datetime
    change_kind: str  # "updated" | "deleted"
    #: On a merge ``deleted`` event, the surviving winner the loser was merged into
    #: (power-map#235); ``None`` on a genuine delete or an ``updated`` event. Lets the
    #: engine re-anchor any entity type to the winner deterministically — no per-entity
    #: identifier re-match, no fuzz (usa-wa#37).
    merged_into: ULID | None = None


@dataclass(frozen=True)
class ChangePage:
    """A page of the changes feed plus the integer cursor to resume from.

    ``next_after`` is the outbox ``seq_id`` to pass as ``after`` on the next pull
    (PM #203; the old timestamp ``next_since`` cursor was retired). ``None`` only
    when PM omits it (defensive); normally PM always echoes a usable seq.
    """

    items: Sequence[ChangeItem]
    next_after: int | None


@dataclass(frozen=True)
class EntityPage:
    """A page of a full-reconcile read (list endpoint)."""

    records: Sequence[dict]
    cursor: str | None


@dataclass(frozen=True)
class DiscoveredEntity:
    """One candidate from ``GET /api/v1/subscriptions/discover`` (PM #203).

    The graph-traversal result the client turns into a subscription. ``entity_type``
    matches PM's feed/discovery vocabulary (``jurisdiction``/``organization``/
    ``role``/``role_assignment``/``person``); the reconciler routes it to a
    descriptor by that string.
    """

    entity_type: str
    entity_id: ULID
    display_name: str | None
    hops_from_root: int


@dataclass(frozen=True)
class SubscriptionResult:
    """Outcome of ``POST /api/v1/subscriptions`` — a bulk, idempotent register.

    ``not_found`` lists ids PM could not resolve to a known entity; the rest of the
    batch is still applied (PM #203).
    """

    registered: int
    already_subscribed: int
    not_found: Sequence[ULID]


class PowerMapClient(Protocol):
    """The PM surface the engine needs. Implemented for real in step 5."""

    async def get_changes(self, after: int | None, limit: int = 100) -> ChangePage:
        """Incremental change feed for ids ``> after`` (None → from the start, seq 0).

        Subscription-filtered (PM #203): returns only changes for entities this key
        is subscribed to. An empty subscription set yields an empty feed.
        """
        ...

    async def discover(
        self,
        *,
        root_type: str,
        root_id: str,
        follow: Sequence[str],
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[DiscoveredEntity]:
        """Graph-traverse from a root entity, returning subscription candidates.

        Implementations paginate internally (PM ``limit``/``offset``) and return the
        full flattened result. ``follow`` is the set of edge types to traverse
        (``lineage``, ``affiliated_orgs``, ``org_children``, ``roles``,
        ``assignments``, ``people``). Read-only; any valid key.
        """
        ...

    async def list_subscriptions(self, *, entity_type: str | None = None) -> Sequence[ULID]:
        """Return the entity ids this key is currently subscribed to (paginated)."""
        ...

    async def add_subscriptions(self, entity_ids: Sequence[ULID]) -> SubscriptionResult:
        """Bulk-register subscriptions (idempotent). Requires ``subscriptions:write``."""
        ...

    async def remove_subscriptions(self, entity_ids: Sequence[ULID]) -> int:
        """Bulk-remove subscriptions; returns the count removed. Requires
        ``subscriptions:write``. Defined for surface completeness — pruning is
        deferred (additive-only sync), so the engine does not call this yet."""
        ...

    async def list_entities(self, read_path: str, params: dict | None = None) -> EntityPage:
        """One page of a full-reconcile read against an entity list endpoint."""
        ...

    async def get_entity(self, read_path: str, pm_id: ULID) -> dict | None:
        """Fetch one full entity record by PM id (feed gives ids, not records)."""
        ...

    async def list_entity_events(self, read_path: str, pm_id: ULID) -> list[dict]:
        """All entity-event records for a parent person/org (the ``/{id}/events``
        sub-resource). ``read_path`` is the parent's read path (``/api/v1/people`` or
        ``/api/v1/orgs``); the wrapper dispatches to the matching events route and
        paginates the full set. Empty list if the parent has no events (or is gone)."""
        ...

    async def search_entities(
        self,
        search_path: str,
        *,
        q: str | None = None,
        identifier_type: str | None = None,
        identifier_value: str | None = None,
        jurisdiction: str | None = None,
        limit: int = 20,
    ) -> EntityPage:
        """Search an entity surface (people/orgs) by name (``q``), identifier, and/or
        jurisdiction. Powers the PM-first match cascade: an exact identifier lookup
        first, then a single FTS name query (power-map#201) confirmed client-side.
        Returns the matching summary records (id + name + …); the caller fetches full
        detail by id. Single-page: PM's FTS returns a small ranked set, so the cascade
        does not page (``EntityPage.cursor`` is always ``None`` here)."""
        ...

    async def post_observation(self, observe_path: str, payload: dict) -> ObservationResult:
        """Submit an observation; return the disposition + anchored id."""
        ...
