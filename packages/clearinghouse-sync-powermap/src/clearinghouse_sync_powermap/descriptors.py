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
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from ulid import ULID

from clearinghouse_core.logging import get_logger

logger = get_logger(__name__)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def parse_pm_timestamp(value: str | None) -> datetime | None:
    """Parse a PM ISO-8601 timestamp (``...Z``) into an aware datetime, or ``None``.

    Shared so descriptors don't each redefine a private ``_parse_ts``; the engine
    and concrete descriptors compare PM clocks (``updated_at``) and mirror PM
    instants (``archived_at``) through the same parser.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def as_ulid(value: Any) -> ULID:
    """Coerce a PM id (str or ULID) to a ULID. Public — reused by siblings/tests."""
    return value if isinstance(value, ULID) else ULID.from_str(str(value))


def _unaccent(text: str) -> str:
    """Strip diacritics (``José`` → ``Jose``) via NFKD decomposition + combining-mark
    removal — mirrors PM's ``unaccent`` FTS config (``pm_unaccent_simple``, #201)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_name(name: str) -> str:
    """Fold a name for PM-vs-local matching in the ``pm_match`` cascade.

    PM curates formal canonical names whose conventions differ from raw adapter
    strings — ``&`` written out as ``and``, accents (``José`` vs ``Jose``), and
    varied punctuation/spacing. This is the **precision** confirm behind PM's FTS
    candidate search (#201), so it must fold the same way PM's FTS configs do.
    Normalization: casefold, unaccent (mirrors ``pm_unaccent_simple``), ``&`` →
    ``and``, then collapse every run of non-alphanumerics to a single space and
    strip. So "Consumer Protection & Business Committee" ≡
    "Consumer Protection and Business Committee", and "José García" ≡ "Jose Garcia".

    NOTE: unaccent runs *before* the ``[^a-z0-9]`` collapse — otherwise accented
    letters (non-ASCII) would be shredded into separators ("José" → "jos ").
    """
    folded = _unaccent(name.casefold()).replace("&", " and ")
    return _NON_ALNUM.sub(" ", folded).strip()


#: Which side is authoritative for *identity* (id minting / system-of-record).
#: Field-level conflicts are resolved by last-write-wins with ties going to PM
#: (see the engine); ``authority`` is descriptive of producer direction only.
Authority = Literal["pm", "local"]

#: How this entity is read from PM. **Mechanism only** — which reconcile *backstop*
#: (if any) also runs is a separate axis, :attr:`reconcile_mode`.
#:   ``feed``      — read off the ``/changes`` feed. All live entities use this
#:                   (jurisdictions joined the feed in PM #179; roles/assignments
#:                   gained feed reads). The ``reconcile`` value is currently
#:                   unused by any production descriptor.
#:   ``reconcile`` — full-list reconcile is the *primary* read (no feed surface).
#:   ``none``      — no PM read surface (e.g. dormant entity-events).
#:
#: NOTE: ``feed`` describes the primary read; it does **not** by itself imply a
#: reconcile backstop. The backstop kind is chosen by :attr:`reconcile_mode`.
ReadSource = Literal["feed", "reconcile", "none"]

#: Which reconcile *backstop* a descriptor runs — the axis :data:`ReadSource` does
#: not capture (``ReadSource`` is the *primary* read mechanism; this is the periodic
#: drift-recovery backstop layered on top). The two were historically conflated in a
#: single ``reconcile_enabled`` boolean (#13); they are now first-class:
#:   ``none``            — no reconcile backstop. The entity's primary read (the
#:                         subscription-filtered feed + the discovery/subscription
#:                         backstop, post-usa-wa#10) is the only refresh path.
#:                         Jurisdictions use this — the WA subtree is driven by the
#:                         SubscriptionReconciler, not a full-list enumeration.
#:   ``full_list``       — full enumeration of :attr:`read_path`, applying every
#:                         record under LWW. The legacy backstop, meaningful only for
#:                         full-mirror entities with a bounded PM list. **No usa-wa
#:                         descriptor uses it** post-#10; preserved for siblings.
#:   ``anchored_cohort`` — bounded backstop for cohort-only *producers* (orgs,
#:                         persons, roles, assignments): re-fetch only OUR anchored
#:                         rows (anchor ``IS NOT NULL``) by id, applying each under
#:                         LWW. O(our cohort), never O(PM-world). Recovers a curation
#:                         edit whose feed event was dropped — the additive discovery
#:                         backstop backfills only *new* ids, so without this an
#:                         already-anchored row that misses its feed bump goes stale
#:                         forever (CannObserv/usa-wa#13).
ReconcileMode = Literal["none", "full_list", "anchored_cohort"]


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
    #: Which reconcile backstop (if any) this entity runs (see :data:`ReconcileMode`).
    #: Default ``none`` — a descriptor opts into ``full_list`` or ``anchored_cohort``
    #: explicitly. This is the first-class successor to the old ``reconcile_enabled``
    #: boolean, which overloaded "does a backstop run" with "which backstop"; that
    #: boolean now derives from this mode (see :attr:`reconcile_enabled`).
    reconcile_mode: ReconcileMode = "none"
    #: Whether the outbox worker may push this entity. Dormant types stay False.
    write_enabled: bool = False
    #: PM-native internal identifier type for enrich-on-match (power-map#198) — e.g.
    #: ``"pm_org_id"``. When set, a row matched to an identifier-less PM record *by
    #: name* enqueues an enrich observation that attaches our identifiers/names to
    #: that PM entity. ``None`` disables enrichment (roles/assignments match
    #: structurally; jurisdictions are PM-authoritative).
    enrich_identifier_type: str | None = None
    #: Observation fields :meth:`to_enrich_observation` carries through from the
    #: :meth:`to_observation` base payload (when present). Append-only evidence PM
    #: lacks — never PM-curated state (parent, affiliations). Default carries only
    #: typed-name evidence; a descriptor with extra source-only facts (e.g. an org's
    #: acronym/phone) extends this tuple. Keeps the portable layer jurisdiction-
    #: agnostic — field vocabulary lives on the concrete sibling descriptor.
    enrich_carry_fields: tuple[str, ...] = ("names",)
    #: Full-reconcile cadence (backstop; default hourly).
    reconcile_cadence: timedelta = timedelta(hours=1)
    #: Column holding the local terminal-delete tombstone (e.g. ``"deleted_at"``),
    #: stamped when a dead anchor resolves to no surviving PM winner (a genuine delete,
    #: not a merge). Deleted rows are excluded from the un-anchored sweep and the
    #: anchored-cohort reconcile, so a deliberately-deleted entity is never re-created
    #: or re-fetched. ``None`` → no delete marker (terminal retirement disabled).
    deleted_column: str | None = None
    #: Column holding the local mirror of PM's reversible ``archived_at`` (e.g.
    #: ``"archived_at"``), set/cleared by :meth:`mirror_archival`. Unlike
    #: :attr:`deleted_column`, an archived row keeps a **live** anchor and is therefore
    #: *kept in* the sweep/reconcile cohort so a dropped un-archive event self-heals
    #: (usa-wa#42). ``None`` → PM archival not mirrored (e.g. identifier-only entities).
    archived_column: str | None = None
    #: Whether this descriptor can re-resolve a dead anchor to its merge-winner via
    #: :meth:`rematch_anchor`. Consulted **on the backstop path only** — a re-fetch 404,
    #: or a bare ``deleted`` feed event with no ``merged_into``. When PM names the winner
    #: (``merged_into``, power-map#235) the engine re-anchors any entity type generically
    #: without this. When False *and* no ``merged_into`` is available, the engine logs an
    #: unhealed dead anchor and leaves the row, rather than retiring a possibly-merged row
    #: with no signal (merge-orphan self-heal, usa-wa#31 / #37).
    supports_rematch: bool = False

    # --- concrete helpers (shared, not overridden) ---------------------------

    @property
    def reconcile_enabled(self) -> bool:
        """Back-compat shim: True iff *any* reconcile backstop runs for this entity.

        Derived from :attr:`reconcile_mode` (``!= "none"``). Kept so call sites that
        only ask "does a backstop run at all?" need not re-spell the mode check. New
        code that must branch on *which* backstop reads :attr:`reconcile_mode`.
        """
        return self.reconcile_mode != "none"

    def anchor_value(self, row: Any) -> ULID | None:
        """The current PM anchor id on a local row, or None if unsynced."""
        return getattr(row, self.anchor_column)

    def anchor_column_expr(self) -> Any:
        """The mapped column object for the PM anchor (e.g. ``Model.pm_org_id``).

        The engine filters/keysets on this for the un-anchored sweep and the
        anchored-cohort reconcile, so neither has to hardcode a per-entity column
        name — the anchor column differs per entity but is known to the descriptor.
        """
        return getattr(self.model, self.anchor_column)

    def set_anchor(self, row: Any, pm_id: ULID) -> None:
        """Write the PM anchor id back onto a local row."""
        setattr(row, self.anchor_column, pm_id)

    async def _anchor_match(self, session: Any, record: dict) -> Any | None:
        """Resolve the local row a PM record maps to **by anchor**, tolerant of a
        duplicate that violates the one-row-per-PM-anchor invariant (usa-wa#86).

        The shared body of every anchor-keyed descriptor's :meth:`local_match`.
        The invariant is enforced at the DB layer (a partial unique index on the
        anchor column); this is the read-side defense in depth for any pre-index
        duplicate that predates it. A plain ``scalar_one_or_none`` would raise
        ``MultipleResultsFound`` on such a pair and poison the whole reconcile/feed
        apply path (the #84 failure mode). Instead we log ``anchor_invariant_violation``
        with both ``source_id``s and return a **deterministic** winner — newest
        ``updated_at``, ``id`` as tiebreak (``updated_at`` alone can tie between
        duplicate spans, and a non-deterministic winner would make LWW flap).

        Requires ``self.model`` to expose ``updated_at`` and ``id`` (the ordering
        keys) — true for every anchor-keyed descriptor's model (all carry
        ``TimestampMixin`` + an ``id`` PK). A future anchor-keyed descriptor over a
        model lacking either would need its own ``local_match`` (or those columns).
        """
        pm_id = self.pm_id_from_record(record)
        if pm_id is None:
            return None
        rows = (
            (
                await session.execute(
                    select(self.model)
                    .where(self.anchor_column_expr() == pm_id)
                    .order_by(self.model.updated_at.desc(), self.model.id.desc())
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None
        if len(rows) > 1:
            logger.error(
                "anchor_invariant_violation",
                extra={
                    "entity_type": self.entity_type,
                    "anchor_column": self.anchor_column,
                    "pm_id": str(pm_id),
                    "source_ids": [getattr(r, "source_id", None) for r in rows],
                    "winner_id": str(rows[0].id),
                },
            )
        return rows[0]

    def deleted_column_expr(self) -> Any:
        """The mapped column for the terminal-delete tombstone (e.g. ``Model.deleted_at``).

        The engine filters the un-anchored sweep and the anchored-cohort reconcile on
        ``IS NULL`` of this so a deleted row is never re-created or re-fetched. An
        *archived* row (``archived_column`` set, this NULL) keeps a live anchor and so
        stays in the cohort — that is the usa-wa#42 fix.
        """
        return getattr(self.model, self.deleted_column)

    def is_deleted(self, row: Any) -> bool:
        """Whether a local row carries a terminal-delete tombstone."""
        return self.deleted_column is not None and getattr(row, self.deleted_column) is not None

    def is_archived(self, row: Any) -> bool:
        """Whether a local row carries the reversible PM-archival mirror."""
        return self.archived_column is not None and getattr(row, self.archived_column) is not None

    def mark_deleted(self, row: Any, now: datetime) -> None:
        """Stamp a row's terminal-delete tombstone — PM deleted it with no surviving
        winner. Clears any ``archived_column`` too: a genuine delete supersedes the
        reversible archived axis (a deleted id is gone from PM, so it can never
        un-archive)."""
        setattr(row, self.deleted_column, now)
        if self.archived_column is not None:
            setattr(row, self.archived_column, None)

    def mirror_archival(self, row: Any, record: dict) -> None:
        """Mirror PM's reversible ``archived_at`` onto the local ``archived_column`` —
        set when archived, cleared on un-archive (set-or-clear, PM's own clock,
        mirroring LWW). A no-op when the entity doesn't mirror archival
        (:attr:`archived_column` is ``None``), so identifier-only/PM-authoritative
        entities (jurisdictions) are unaffected even if PM sends ``archived_at``.

        Called from a descriptor's :meth:`upsert_from_pm` so every archival-mirroring
        cache row drops out of live reads when PM inactivates it (usa-wa#40 orgs;
        #41 person/role/assignment). PM owns the inactivation decision (incl.
        dormant-vs-abolished) — ``authority = "pm"``; this only mirrors.

        Distinct from :attr:`deleted_column` (the terminal tombstone): an archived
        entity keeps a **live** PM id. So both feed paths *and* the anchored-cohort
        reconcile re-fetch it — clearing on un-archive happens on whichever arrives
        first, and a dropped un-archive feed event is recovered by the next reconcile
        (usa-wa#42, the bug the deleted/archived split closed). A genuine-delete /
        merge-orphan id is gone from PM, so it lives on :attr:`deleted_column`, never
        here — and is never re-fetched.
        """
        if self.archived_column is None:
            return
        setattr(row, self.archived_column, parse_pm_timestamp(record.get("archived_at")))

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

    async def rematch_anchor(self, client: Any, session: Any, row: Any) -> ULID | None:
        """Re-resolve a dead-anchored row to its surviving PM **merge-winner**.

        The **backstop path only**: when PM names the winner (``merged_into``,
        power-map#235) the engine re-anchors without this. This covers the gap when no
        ``merged_into`` was seen — a re-fetch 404, or a bare ``deleted`` feed event for a
        rematch-capable descriptor — so the winner is re-resolved by **identifier only**
        (never name/hierarchy fuzz — re-anchoring a produced row to the wrong entity is
        worse than retiring it). Returns the winner PM id, or ``None`` when no identifier
        winner exists (→ the engine retires the row). Only consulted when
        :attr:`supports_rematch` is True; default ``None``.
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

    @staticmethod
    def record_has_identifier_type(record: dict, id_type: str) -> bool:
        """Whether a PM record's ``identifiers[]`` holds **any** identifier of
        ``id_type`` (value-agnostic sibling of :meth:`record_has_identifier`).

        Used by the org name-match guard: a same-name candidate carrying *any*
        identifier of our type is claimed by a different entity of our class, so we
        must not adopt it. Keeps the ``identifiers[].type_slug`` payload shape in one
        place.
        """
        for ident in record.get("identifiers") or []:
            if ident.get("type_slug") == id_type:
                return True
        return False

    async def to_enrich_observation(self, session: Any, row: Any) -> dict:
        """Build the enrich observation for an already-matched, anchored row.

        Derives from :meth:`to_observation` but re-keys the top-level identifier to
        :attr:`enrich_identifier_type` + the row's PM anchor (so PM attaches by id
        instead of resolving by our identifier, per power-map#198), and demotes the
        row's real identifier to an ``additional_identifiers`` entry to append.

        Deliberately **narrow**: only the identifier and the descriptor's declared
        :attr:`enrich_carry_fields` (typed-name evidence by default, plus any
        source-only facts PM lacks) ride along. Other observation fields (org
        parent, jurisdiction affiliations) are *not* re-asserted — they belong to
        how PM curates the entity (which we adopted on match), and enrich conveys
        only the evidence we hold. Append-only, idempotent.
        """
        base = await self.to_observation(session, row)
        real_type = base.pop("identifier_type", None)
        real_value = base.pop("identifier_value", None)
        payload: dict[str, Any] = {
            "identifier_type": self.enrich_identifier_type,
            "identifier_value": str(self.anchor_value(row)),
        }
        # Preserve any cross-source additional identifiers ``to_observation`` emitted
        # (e.g. a person's child identifier schemes) and append the demoted real
        # primary — both attach to the anchored PM entity. Append-only, idempotent.
        additional = list(base.pop("additional_identifiers", None) or [])
        if real_type and real_value:
            additional.append({"identifier_type_slug": real_type, "identifier_value": real_value})
        if additional:
            payload["additional_identifiers"] = additional
        for field in self.enrich_carry_fields:
            if base.get(field):
                payload[field] = base[field]
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
