"""The per-entity contract a sibling service implements.

An :class:`EntityDescriptor` wires one local table to its Power Map endpoints
and supplies the three behaviours the engine cannot know generically: how to
shape an observation payload, how to upsert a PM record into the local cache,
and where each side's "last updated" clock lives (for last-write-wins
reconciliation).

Static configuration is declared as class attributes; behaviour is three
abstract methods. The engine treats descriptors opaquely — it never imports a
concrete one.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal

from ulid import ULID

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def as_ulid(value: Any) -> ULID:
    """Coerce a PM id (str or ULID) to a ULID. Public — reused by siblings/tests."""
    return value if isinstance(value, ULID) else ULID.from_str(str(value))


def normalize_name(name: str) -> str:
    """Fold a name for PM-vs-local fuzzy matching in the ``pm_match`` cascade.

    PM curates formal canonical names with conventions that differ from raw
    adapter strings — notably ``&`` written out as ``and`` and varied
    punctuation/spacing. Normalization: casefold, ``&`` → ``and``, then collapse
    every run of non-alphanumerics to a single space and strip. So
    "Consumer Protection & Business Committee" and
    "Consumer Protection and Business Committee" both fold to
    ``consumer protection and business committee``.
    """
    folded = name.casefold().replace("&", " and ")
    return _NON_ALNUM.sub(" ", folded).strip()


#: Which side is authoritative for *identity* (id minting / system-of-record).
#: Field-level conflicts are resolved by last-write-wins with ties going to PM
#: (see the engine); ``authority`` is descriptive of producer direction only.
Authority = Literal["pm", "local"]

#: How this entity is read from PM.
#:   ``feed``      — primary path is the ``/changes`` feed (person/org today).
#:   ``reconcile`` — primary path is the full-list reconcile (jurisdictions —
#:                   not on the feed).
#:   ``none``      — no PM read surface yet (roles/assignments/entity_events).
ReadSource = Literal["feed", "reconcile", "none"]


class EntityDescriptor(ABC):
    """Contract binding one local model to its PM sync behaviour."""

    #: Stable discriminator; must match the ``entity_type`` PM emits on the feed.
    entity_type: str
    #: The SQLAlchemy model class for the local cache table.
    model: type[Any]
    #: Column holding the PM anchor id (e.g. ``"pm_jurisdiction_id"``).
    anchor_column: str
    #: Columns forming the local natural key (for idempotent upsert).
    natural_key: Sequence[str]
    #: Producer side. **Descriptive only** — the engine resolves field conflicts
    #: by last-write-wins with ties going to PM, regardless of this value. It
    #: documents which side mints identity and biases nothing in code today; wire
    #: a consumer before relying on it.
    authority: Authority = "local"
    #: PM list endpoint for full reconcile (e.g. ``"/api/v1/jurisdictions"``).
    read_path: str | None = None
    #: PM observation endpoint (e.g. ``"/api/v1/jurisdictions/observations"``).
    observe_path: str | None = None
    #: Read strategy (see :data:`ReadSource`).
    read_source: ReadSource = "none"
    #: Whether the outbox worker may push this entity. Dormant types stay False.
    write_enabled: bool = False
    #: PM-native internal identifier type for enrich-on-match (power-map#198) — e.g.
    #: ``"pm_org_id"``. When set, a row matched to an identifier-less PM record *by
    #: name* enqueues an enrich observation that attaches our identifiers/names to
    #: that PM entity. ``None`` disables enrichment (roles/assignments match
    #: structurally; jurisdictions are PM-authoritative).
    enrich_identifier_type: str | None = None
    #: Full-reconcile cadence (backstop; default hourly).
    reconcile_cadence: timedelta = timedelta(hours=1)

    # --- concrete helpers (shared, not overridden) ---------------------------

    def anchor_value(self, row: Any) -> ULID | None:
        """The current PM anchor id on a local row, or None if unsynced."""
        return getattr(row, self.anchor_column)

    def set_anchor(self, row: Any, pm_id: ULID) -> None:
        """Write the PM anchor id back onto a local row."""
        setattr(row, self.anchor_column, pm_id)

    def natural_key_values(self, row: Any) -> tuple[Any, ...]:
        """The local natural-key tuple for a row (used for upsert matching)."""
        return tuple(getattr(row, col) for col in self.natural_key)

    def pm_id_from_record(self, record: dict) -> ULID | None:
        """Extract the PM anchor id from a PM record.

        Default reads ``record["id"]``; override if PM names the id differently
        for this entity. Used to capture the anchor even when LWW keeps the local
        row (so the cache doesn't look unsynced).
        """
        raw = record.get("id")
        return as_ulid(raw) if raw is not None else None

    async def fetch_record(self, client: Any, pm_id: Any) -> dict | None:
        """Fetch the full PM record for a feed item.

        Default delegates to ``client.get_entity(read_path, pm_id)``. Person/org
        override this to also pull their ``/{id}/events`` sub-resource so the
        local entity-events mirror stays current (a feed bump on the parent may
        be an event change).
        """
        return await client.get_entity(self.read_path, pm_id)

    # --- behaviour (sibling implements) --------------------------------------

    @abstractmethod
    async def to_observation(self, session: Any, row: Any) -> dict:
        """Build the PM observation payload for a local row.

        Async + session-bound because the payload may need related rows the
        engine hasn't loaded (e.g. a jurisdiction's type slug, an assignment's
        person/role PM anchors).
        """

    @abstractmethod
    async def local_match(self, session: Any, record: dict) -> Any | None:
        """Find the existing local row a PM record maps to, or None.

        The descriptor owns the PM-record → local-natural-key mapping (PM may key
        on ``slug`` where the local cache keys on ``source``/``source_id``). The
        engine calls this *before* upsert so it can compare timestamps for LWW.
        """

    @abstractmethod
    async def upsert_from_pm(self, session: Any, record: dict, existing: Any | None = None) -> Any:
        """Idempotently upsert a PM record into the local cache (set anchor).

        Returns the upserted row. Safe to call whether or not a row already
        exists — it must find-or-create on the natural key. ``existing`` is an
        optional already-resolved local row (from :meth:`local_match`) the caller
        may pass to avoid a redundant lookup; implementations should use it when
        provided and fall back to their own find when ``None``.
        """

    @abstractmethod
    def last_updated(self, obj: Any) -> datetime | None:
        """Return the UTC 'last updated' clock for a local row OR a PM record.

        Both sides use ``updated_at`` (the local ``TimestampMixin`` column and
        the PM record's own field); the descriptor encapsulates the lookup so the
        engine can compare the two for LWW. The local value is kept at parity with
        PM by :meth:`set_last_updated` on import.
        """

    async def pm_match(self, client: Any, session: Any, row: Any) -> Any | None:
        """Find this local row's pre-existing PM entity *before* creating a new one.

        PM is the system of record, and it holds curated records that may carry
        none of the identifiers usa-wa keys on (e.g. the backfilled WA org tree).
        Observing such a row by identifier would mint a **duplicate**. So before
        the un-anchored sweep enqueues a CREATE, the engine consults this cascade:
        exact identifier → normalized name (+ jurisdiction) → parent-hierarchy
        scope. Return the matched PM id to anchor against (no create), or ``None``
        when the record is genuinely new (→ observe-create).

        Default ``None`` keeps identifier-only entities (jurisdictions) on the
        plain observe path. Override for orgs/persons (see :func:`normalize_name`).
        """
        return None

    async def needs_enrich(self, record: dict, row: Any) -> bool:
        """Whether the matched PM ``record`` lacks an identifier this row holds and
        should receive (enrich-on-match, power-map#198).

        Called by the engine after a successful ``pm_match`` + ``upsert_from_pm``,
        only when :attr:`enrich_identifier_type` is set. ``True`` → the engine
        enqueues an ``ENRICH`` outbox entry. Default ``False`` (no enrichment).
        Producers that match identifier-less PM records by name (orgs, persons)
        override this, typically via :meth:`record_has_identifier`.
        """
        return False

    @staticmethod
    def record_has_identifier(record: dict, id_type: str, value: str) -> bool:
        """Whether a PM record's ``identifiers[]`` already holds ``(id_type, value)``.

        Shared helper for :meth:`needs_enrich`: enrichment is skipped (idempotent)
        when PM already carries our identifier — e.g. a row matched *by identifier*
        rather than by name.
        """
        for ident in record.get("identifiers") or []:
            if ident.get("type_slug") == id_type and ident.get("value") == value:
                return True
        return False

    async def to_enrich_observation(self, session: Any, row: Any) -> dict:
        """Build the enrich observation for an already-matched, anchored row.

        Reuses :meth:`to_observation` but re-keys the top-level identifier to
        :attr:`enrich_identifier_type` + the row's PM anchor (so PM attaches by id
        instead of resolving by our identifier, per power-map#198), and demotes the
        row's real identifier to an ``additional_identifiers`` entry to append. All
        other observation fields (names, affiliations, parent) ride along append-only.
        """
        base = await self.to_observation(session, row)
        real_type = base.pop("identifier_type", None)
        real_value = base.pop("identifier_value", None)
        payload: dict[str, Any] = {
            "identifier_type": self.enrich_identifier_type,
            "identifier_value": str(self.anchor_value(row)),
        }
        if real_type and real_value:
            payload["additional_identifiers"] = [
                {"identifier_type_slug": real_type, "identifier_value": real_value}
            ]
        payload.update(base)
        return payload

    async def dependencies_ready(self, session: Any, row: Any) -> bool:
        """Whether this row's PM prerequisites are anchored, so an observation can be built.

        Roles need their organization's PM id; assignments need their person's and
        role's PM ids. The engine consults this *before* delivering an outbox entry
        and **defers** (leaves the entry PENDING, bumps ``next_attempt_at``) when
        False — the ordering self-resolves as the parents anchor in later cycles,
        without a crash or a rejection.

        Default ``True`` for self-sufficient entities (jurisdictions, orgs).
        """
        return True

    def set_last_updated(self, obj: Any, value: datetime) -> None:
        """Stamp a freshly-cached local row's LWW clock with the remote (PM) time.

        The engine calls this after :meth:`upsert_from_pm` so the row reads at
        parity with PM rather than a local ``now()``. Without it the next
        reconcile judges the row locally-newer and enqueues a spurious write-back
        (the go-live 403 loop). A genuine local edit still bumps ``updated_at``
        and correctly wins LWW.

        Pairs with :meth:`last_updated`: this writes the same column that one
        reads. Override **both or neither** — a descriptor whose local clock is
        not ``updated_at`` must override both, or the engine will write one column
        while LWW compares another and the write-back loop silently returns.
        """
        obj.updated_at = value
