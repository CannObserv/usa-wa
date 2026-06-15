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


@dataclass(frozen=True)
class ChangePage:
    """A page of the changes feed plus the cursor to resume from."""

    items: Sequence[ChangeItem]
    cursor: str | None


@dataclass(frozen=True)
class EntityPage:
    """A page of a full-reconcile read (list endpoint)."""

    records: Sequence[dict]
    cursor: str | None


class PowerMapClient(Protocol):
    """The PM surface the engine needs. Implemented for real in step 5."""

    async def get_changes(self, since: str | None, limit: int = 100) -> ChangePage:
        """Incremental change feed since the given cursor."""
        ...

    async def list_entities(self, read_path: str, params: dict | None = None) -> EntityPage:
        """One page of a full-reconcile read against an entity list endpoint."""
        ...

    async def get_entity(self, read_path: str, pm_id: ULID) -> dict | None:
        """Fetch one full entity record by PM id (feed gives ids, not records)."""
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
