"""Organization descriptor — PM-first producer of the WA legislative org tree.

PM is the system of record and has **already backfilled** the full WA org tree
(Legislature → chambers → committees) with curated canonical names + a
``parent_id`` hierarchy, but every backfilled org carries an **empty
``identifiers`` list**. So an identifier-keyed observation cannot auto-attach to
them; producing orgs by identifier would mint duplicates of PM's curated tree.

The descriptor therefore runs a **match-before-create cascade** (:meth:`pm_match`)
over every un-anchored adapter row before the engine enqueues a CREATE:

1. **Identifier** — ``orgs/search?identifier_type=…&identifier_value=…`` (the only
   server-side exact filter PM honours).
2. **Name** — PM's server-side FTS (``q`` + ``jurisdiction``; power-map#199/#201)
   does word-token matching that folds punctuation, ``&``, and word order, then we
   confirm by :func:`normalize_name` equality (the precision gate). FTS subsumes the
   ``&``/punctuation cases that the earlier ILIKE cohort-scan fallback existed for,
   so no enumeration fallback is needed.
3. **Hierarchy** — when the local parent is anchored, disambiguate same-name
   candidates by PM ``parent_id``.

Matched → adopt PM's canonical name + jurisdiction + anchor; **no PM write**
(PM already has it). Genuinely new → observe-create.

Read strategy is ``feed`` but **update-only**: a feed change to an org we have
already anchored is applied (so we stay responsive to PM renames); a feed change
to an org we have never produced is **skipped** (``upsert_from_pm`` returns
``None`` when ``existing`` is absent) rather than broadly mirrored — that keeps
the local cache to the produced cohort and avoids the duplicate that a mirror
row + a later adapter row would create. Bounding the feed itself to the WA subset
is tracked in CannObserv/usa-wa#10.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_sync_powermap.descriptors import (
    EntityDescriptor,
    as_ulid,
    normalize_name,
    parse_pm_timestamp,
)
from usa_wa_sync_powermap.descriptors.events import sync_entity_events
from usa_wa_sync_powermap.descriptors.org_acronyms import sync_org_acronyms
from usa_wa_sync_powermap.descriptors.org_names import sync_org_names

logger = get_logger(__name__)

#: PM jurisdiction slug that scopes the name-match search to the WA cohort.
JURISDICTION_SLUG = "usa-wa"
#: PM search surface for orgs.
SEARCH_PATH = "/api/v1/orgs/search"
#: The org↔jurisdiction affiliation that means "is governed by" (PM #194); its
#: ``jurisdiction_id`` equals our local ``pm_jurisdiction_id``.
GOVERNING = "governing"
#: Human-readable ``display_label`` for a phone contact_method, by org_type (#31).
#: WSL carries no per-phone label, so we synthesize one — an unlabelled number is
#: unreadable in PM's admin/change-feed. ``_DEFAULT_PHONE_LABEL`` covers org types
#: that lack a specific label (anchors rarely carry a phone, but stay correct if so).
_PHONE_LABEL_BY_ORG_TYPE = {"committee": "Committee Office", "subcommittee": "Committee Office"}
_DEFAULT_PHONE_LABEL = "Main Office"
#: Upper bound on FTS candidates to confirm — and the **recall ceiling**: the exact
#: match must rank within this window or it reads as "new" → a (mergeable) duplicate.
#: Ample headroom today (jurisdiction-scoped FTS AND-of-tokens over a ~120-org
#: cohort returns a handful); revisit if the cohort grows or PM exposes a rank score.
_SEARCH_LIMIT = 50


def identifier_type_for(source: str, org_type: str | None) -> str | None:
    """Map a local org's ``(source, org_type)`` to its PM ``identifier_type`` slug.

    The slug encodes *entity + producing system + key* — ``wa_legislature`` is the
    system, not a jurisdiction (see the identity-sync design D1). Unknown sources
    return ``None`` → no identifier match (the cascade falls through to name).
    """
    if source == "usa_wa_legislature":
        if org_type == "chamber":
            return "org_wa_legislature_chamber"
        if org_type == "legislature":
            return "org_wa_legislature"
        return "org_wa_legislature_committee_id"
    if source == "usa_wa_pdc":
        return "org_wa_pdc"
    return None


def observed_name(row: Any) -> str:
    """The name to assert to PM as ``legal`` evidence.

    The meeting-derived Joint/`Other` class (#39/#61) stores WSL's agency-double-prefixed
    ``LongName`` ("Joint Joint Transportation Committee") as ``name``; its ``short_name``
    is the clean ``Name`` ("Joint Transportation Committee"), which is what PM should
    receive — the meeting serializer is deterministic (``LongName == f"{Agency} {Name}"``).
    Other classes keep ``name``: House/Senate ``short_name`` ("Finance") is too terse to be
    the canonical. Falls back to ``name`` if ``short_name`` is unset. The raw SOAP wire stays
    verbatim (provenance), and local ``Organization.name`` is the verbatim LongName *as
    produced* — though the read mirror still adopts PM's curated canonical into it
    (``apply_record`` → ``upsert_from_pm``). This only shapes the PM-facing name *evidence*;
    PM still curates ``is_canonical``."""
    if row.org_type == "other" and row.short_name:
        return row.short_name
    return row.name


class OrganizationDescriptor(EntityDescriptor):
    """Binds ``clearinghouse_domain_legislative.identity.Organization`` to PM."""

    entity_type = "organization"
    model = Organization
    anchor_column = "pm_organization_id"
    # Local idempotency is on the natural key, but PM-record → local-row mapping
    # keys on the anchor (PM's backfilled orgs have no usa-wa identifier to derive
    # ``source``/``source_id`` from) — see :meth:`local_match`.
    natural_key = ("source", "source_id")
    deleted_column = "deleted_at"  # terminal merge-orphan / genuine-delete tombstone (#31)
    archived_column = "archived_at"  # PM reversible archival mirror (#40/#42)
    supports_rematch = True  # can re-resolve a dead anchor to its merge-winner by id
    authority = "pm"  # PM is system-of-record for the org tree
    read_path = "/api/v1/orgs"
    observe_path = "/api/v1/orgs/observations"
    read_source = "feed"
    # Cohort-only producer: feed is the primary read; the bounded anchored-cohort
    # backstop re-fetches only OUR anchored rows to recover dropped feed events (#13).
    reconcile_mode = "anchored_cohort"
    write_enabled = True
    enrich_identifier_type = "pm_org_id"  # enrich-on-match (#198)
    # Acronym/phone are WSL-sourced facts PM lacks — attach them on match, same
    # rationale as the identifier itself (#25). Parent/affiliations stay excluded.
    enrich_carry_fields = ("names", "org_acronyms", "contact_methods")

    def __init__(self, *, search_match_cap: int | None = None) -> None:
        """``search_match_cap`` (#12): the name-match candidate window passed as the
        search ``limit``. ``None`` keeps the historical default (:data:`_SEARCH_LIMIT`);
        the registry plumbs an operator override from ``SidecarSettings``."""
        self.search_match_cap = _SEARCH_LIMIT if search_match_cap is None else search_match_cap

    async def needs_enrich(self, record: dict, row: Any) -> bool:
        """Enrich when PM's matched org lacks the identifier we hold for it."""
        id_type = identifier_type_for(row.source, row.org_type)
        return id_type is not None and not self.record_has_identifier(
            record, id_type, row.source_id
        )

    # --- match cascade (PM-first) --------------------------------------------

    async def pm_match(self, client: Any, session: Any, row: Any) -> Any | None:
        # 1. Identifier — exact, server-side. The happy path once PM holds the id.
        # No jurisdiction filter: an (identifier_type, value) pair is globally
        # unique in PM, and scoping it would false-miss an org that holds our id
        # but isn't yet affiliated to usa-wa → a spurious duplicate.
        id_type = identifier_type_for(row.source, row.org_type)
        if id_type is not None:
            page = await client.search_entities(
                SEARCH_PATH,
                identifier_type=id_type,
                identifier_value=row.source_id,
                limit=1,
            )
            for rec in page.records:
                logger.info(
                    "org_pm_match_identifier",
                    extra={"source_id": row.source_id, "pm_id": rec.get("id")},
                )
                return as_ulid(rec["id"])

        # 2. Name — PM FTS (word-token, folds &/punct/order; #199/#201) narrows;
        # normalize_name equality confirms. FTS subsumes the old ILIKE cohort-scan
        # fallback, so a single query suffices. Match on the *asserted* name
        # (observed_name), so matching and observing use the same name — for the
        # Joint/`Other` class that is the clean short_name, not the double-prefixed
        # LongName (#61); searching by the prefixed form would false-miss a PM org a
        # curator created under the clean name and duplicate it.
        match_name = observed_name(row)
        target = normalize_name(match_name)
        page = await client.search_entities(
            SEARCH_PATH, q=match_name, jurisdiction=JURISDICTION_SLUG, limit=self.search_match_cap
        )
        named = [c for c in page.records if normalize_name(c.get("name") or "") == target]
        if len(named) == 1:
            logger.info(
                "org_pm_match_name", extra={"entity_name": match_name, "pm_id": named[0].get("id")}
            )
            return as_ulid(named[0]["id"])

        # 3. Hierarchy — disambiguate same-name candidates by anchored parent.
        if len(named) > 1:
            parent_pm = await self._parent_pm_id(session, row)
            if parent_pm is not None:
                scoped = [c for c in named if c.get("parent_id") == str(parent_pm)]
                if len(scoped) == 1:
                    logger.info(
                        "org_pm_match_hierarchy",
                        extra={"entity_name": match_name, "pm_id": scoped[0].get("id")},
                    )
                    return as_ulid(scoped[0]["id"])
            logger.warning(
                "org_pm_match_ambiguous",
                extra={"entity_name": match_name, "candidates": [c.get("id") for c in named]},
            )

        return None  # genuinely new → observe-create

    async def _parent_pm_id(self, session: Any, row: Any) -> Any | None:
        if row.parent_organization_id is None:
            return None
        parent = await session.get(Organization, row.parent_organization_id)
        return parent.pm_organization_id if parent is not None else None

    async def rematch_anchor(self, client: Any, session: Any, row: Any) -> Any | None:
        """Re-resolve a dead anchor (PM merged the org away) to the surviving winner by
        **identifier only** — the high-precision stage of :meth:`pm_match`, no name/
        hierarchy fuzz. PM transfers our committee identifier to the merge winner, so an
        exact ``(identifier_type, value)`` lookup finds it; a miss → genuine delete
        (the engine retires). Identifier ids are globally unique, so no jurisdiction
        scope (mirrors ``pm_match`` step 1)."""
        id_type = identifier_type_for(row.source, row.org_type)
        if id_type is None:
            return None
        page = await client.search_entities(
            SEARCH_PATH,
            identifier_type=id_type,
            identifier_value=row.source_id,
            limit=1,
        )
        for rec in page.records:
            logger.info(
                "org_rematch_identifier",
                extra={"source_id": row.source_id, "pm_id": rec.get("id")},
            )
            return as_ulid(rec["id"])
        return None

    # --- write path ----------------------------------------------------------

    async def to_observation(self, session: Any, row: Any) -> dict:
        id_type = identifier_type_for(row.source, row.org_type)
        if id_type is None:
            # Unknown source → no PM identifier_type; PM will reject. Surface it
            # (the outbox would otherwise read as a silent failure).
            logger.warning(
                "org_identifier_type_unresolved",
                extra={"source": row.source, "org_type": row.org_type},
            )
        payload: dict[str, Any] = {
            "identifier_type": id_type,
            "identifier_value": row.source_id,
            # Typed name *evidence* — PM curates ``is_canonical``; we never assert it.
            # Joint/`Other` send the clean short_name, not the double-prefixed name (#61).
            "names": [{"name": observed_name(row), "name_type": "legal"}],
        }
        # Single-value local columns → PM's list-shaped fields. Guard on truthiness
        # so an empty-string acronym never lands as ``org_acronyms: [{...}]``; phone is
        # already None-or-nonempty from the normalizer. ``org_acronyms`` is a list of
        # ``{acronym, is_canonical?}`` objects (PM schema; the bare-string form is
        # 422-rejected). We assert the acronym as evidence only — PM curates
        # ``is_canonical`` (default false), the same hands-off stance as ``names``.
        if row.acronym:
            payload["org_acronyms"] = [{"acronym": row.acronym}]
        if row.phone:
            label = _PHONE_LABEL_BY_ORG_TYPE.get(row.org_type, _DEFAULT_PHONE_LABEL)
            payload["contact_methods"] = [
                {"contact_type": "phone", "value": row.phone, "display_label": label}
            ]
        affiliation = await self._governing_affiliation(session, row)
        if affiliation is not None:
            payload["jurisdiction_affiliations"] = [affiliation]
        parent_pm = await self._parent_pm_id(session, row)
        if parent_pm is not None:
            payload["organization_parent_id"] = str(parent_pm)
        return payload

    def to_active_observation(self, row: Any, *, active: bool) -> dict:
        """Build the one-shot **producer active-flag** observation.

        Drives the WSL biennium-absence axis (#44) in **both** directions:
        ``active=False`` retires a committee the current roster dropped, ``active=True``
        reactivates one that reappears. A deliberate producer action, **not** routine
        sync (:meth:`to_observation` keeps ``active`` out to avoid an LWW write-back
        fight with PM's authority over the axis, #43).

        Enrich-keyed by the PM anchor (``pm_org_id``, power-map#198) like
        :meth:`to_enrich_observation`, but asserts **only** ``active`` — no
        curated-evidence fields (names/acronyms/contact/parent) re-ridden, since PM
        already curates the org and this conveys a single state mutation. PM applies
        ``active`` independently of any name evidence (power-map ``submit_org_observation``),
        so an evidence-less payload is accepted. Synchronous — no DB access needed.

        The caller is responsible for the guards this payload cannot enforce: the row
        must be anchored (``pm_org_id`` set), and an org carrying ``archived_at`` must
        be skipped — PM 422s ``active`` on an archived org (``active_on_archived_org``).
        """
        return {
            "identifier_type": self.enrich_identifier_type,
            "identifier_value": str(self.anchor_value(row)),
            "active": active,
        }

    def to_names_observation(
        self, row: Any, *, prior_name: str, new_name: str, boundary: Any
    ) -> dict:
        """Build the one-shot **producer dated-name** observation for a WSL rename (#46).

        A committee keeps a stable WSL ``Id`` while its ``LongName`` changes (usually at a
        biennium boundary); :mod:`reconcile_committee_names` detects that as a rename and
        calls this to emit the windowed name evidence. The boundary is half-open
        ``[effective_start, effective_end)``:

        - **prior name** is typed ``former`` (#58) and carries ``effective_end = boundary``
          only — its true start may be bienniums old and is left to PM (omitted, not nulled).
        - **new name** is typed ``legal`` and carries ``effective_start = boundary`` with an
          open end (the current name).

        ``name_type`` is *observation*, not curation: a rename on a stable WSL ``Id`` is
        direct evidence the prior name is ``former`` and the new one ``legal`` — PM's own
        org-name vocabulary (``dba | former | legal``) models this, so we assert it rather
        than leaving the closure implicit in the window alone (#58 reverses the earlier
        window-only stance). We still never assert ``is_canonical`` — PM curates that and
        resolves the canonical scalar, the same hands-off stance as :meth:`to_observation`'s
        ``names``. Enrich-keyed by the PM anchor
        (``pm_org_id``), like :meth:`to_active_observation`; PM applies dated-name evidence
        independently, so no other curated fields are re-ridden. Synchronous — no DB access.

        Caller guards (this payload can't enforce them): the row must be anchored
        (``pm_org_id`` set) and live (an archived org 422s evidence).
        """
        return {
            "identifier_type": self.enrich_identifier_type,
            "identifier_value": str(self.anchor_value(row)),
            "names": [
                {"name": prior_name, "name_type": "former", "effective_end": boundary.isoformat()},
                {"name": new_name, "name_type": "legal", "effective_start": boundary.isoformat()},
            ],
        }

    async def _governing_affiliation(self, session: Any, row: Any) -> dict | None:
        """Build the ``governing`` affiliation from the local org's jurisdiction.

        Needs the jurisdiction's PM anchor (``pm_jurisdiction_id``); a local
        jurisdiction not yet synced to PM yields no affiliation (omitted, not
        nulled — PM keys affiliations by PM jurisdiction id)."""
        if row.jurisdiction_id is None:
            return None
        jur = await session.get(Jurisdiction, row.jurisdiction_id)
        if jur is None or jur.pm_jurisdiction_id is None:
            return None
        return {
            "jurisdiction_id": str(jur.pm_jurisdiction_id),
            "affiliation_type_slug": GOVERNING,
        }

    # --- read path -----------------------------------------------------------

    async def local_match(self, session: Any, record: dict) -> Any | None:
        """Map a PM record to its local row by **anchor** (``pm_organization_id``).

        PM's backfilled orgs carry no usa-wa identifier, so the local natural key
        cannot be derived from the record; the durable link is the anchor written
        on first match/create. Returns ``None`` for an org we have never
        produced — :meth:`upsert_from_pm` then skips it (update-only)."""
        pm_id = record.get("id")
        if pm_id is None:
            return None
        return (
            await session.execute(
                select(Organization).where(Organization.pm_organization_id == as_ulid(pm_id))
            )
        ).scalar_one_or_none()

    async def fetch_record(self, client: Any, pm_id: Any) -> dict | None:
        """Fetch the PM org and attach its ``/events`` sub-resource (#19).

        See :meth:`PersonDescriptor.fetch_record`; a parent feed bump may carry an
        event change, so the events are embedded for the mirror refresh.

        Note: the dated-name mirror (#45) consumes ``record["names"]`` and the
        acronym mirror (#47) consumes ``record["acronyms"]`` — both ride **embedded**
        in the ``OrgDetail`` payload from ``get_entity``, *not* attached here like
        ``events`` (a separate sub-resource). If PM ever moves either to its own
        endpoint, ``upsert_from_pm``'s ``sync_org_names`` / ``sync_org_acronyms``
        would silently no-op; attach them here then (see ``descriptors/org_names.py``
        and ``descriptors/org_acronyms.py``).
        """
        record = await client.get_entity(self.read_path, pm_id)
        if record is None:
            return None  # parent gone → skip the events fetch entirely
        record["events"] = await client.list_entity_events(self.read_path, pm_id)
        return record

    async def upsert_from_pm(self, session: Any, record: dict, existing: Any | None = None) -> Any:
        """Apply a PM org record onto the local cache — **update-only**.

        ``existing`` is the already-anchored local row (from the sweep's match or
        :meth:`local_match`). When absent, the org is one we have never produced;
        we return ``None`` (skip) rather than insert a mirror row, which would
        duplicate the adapter row a later sweep anchors to the same PM id.
        """
        row = existing if existing is not None else await self.local_match(session, record)
        if row is None:
            return None
        name = record.get("name")
        if name:  # adopt PM's canonical name; never overwrite PM with ours
            row.name = name
        jurisdiction_id = await self._governing_local_jurisdiction(session, record)
        if jurisdiction_id is not None:
            row.jurisdiction_id = jurisdiction_id
        parent_id = await self._local_parent_id(session, record)
        if parent_id is not None:
            row.parent_organization_id = parent_id
        if record.get("id") is not None:
            row.pm_organization_id = as_ulid(record["id"])
        # Mirror PM's archival (its "inactive" signal) onto the retirement tombstone
        # so the inactivated org drops out of live reads (usa-wa#40); shared across
        # the identity descriptors (usa-wa#41).
        self.mirror_archival(row, record)
        # Mirror PM's ``active`` domain flag (power-map#240/usa-wa#43). PM authority.
        # ``is not None`` guards the detail-only field: a search-shaped record omits
        # ``active``, and a missing key must not clobber the local value. Unlike
        # archival this is NOT a hide gate — the row stays in live reads either way.
        active = record.get("active")
        if active is not None:
            row.active = active
        await session.flush()
        if "events" in record:  # mirror the embedded events sub-resource (#19)
            await sync_entity_events(
                session, entity_kind="organization", entity_id=row.id, pm_events=record["events"]
            )
        if "names" in record:  # mirror the embedded dated-name variants (#45)
            await sync_org_names(session, organization_id=row.id, pm_names=record["names"] or [])
        if "acronyms" in record:  # mirror the embedded acronym variants (#47)
            acronyms = record["acronyms"] or []
            await sync_org_acronyms(session, organization_id=row.id, pm_acronyms=acronyms)
            # Resolve the scalar to PM's canonical acronym — symmetric with ``name``
            # adoption above (#65). PM curates ``is_canonical``; we adopt the entry it
            # marks so ``Organization.acronym`` is the PM-resolved current scalar the #47
            # docstring promises (the child mirror holds every variant). Like ``if name:``
            # we never clobber the produced value with None: PM reporting no canonical
            # (none ``is_canonical``, an empty list, or a search-shaped record with no
            # ``acronyms`` key) leaves the local scalar as-is. Adoption rides the PM-wins
            # branch of ``apply_record`` (this method is called with ``existing`` there),
            # which stamps ``_adopt_remote_clock`` right after — so the next reconcile sees
            # LWW parity, not a local ``now()``, and no spurious org write-back is enqueued.
            canonical = next((a["acronym"] for a in acronyms if a.get("is_canonical")), None)
            if canonical:
                row.acronym = canonical
        return row

    async def _governing_local_jurisdiction(self, session: Any, record: dict) -> Any | None:
        """Resolve the ``governing`` affiliation's PM jurisdiction to a local id."""
        for aff in record.get("jurisdiction_affiliations") or []:
            atype = aff.get("affiliation_type") or {}
            if atype.get("slug") != GOVERNING:
                continue
            pm_jur = aff.get("jurisdiction_id")
            if not pm_jur:
                return None
            jur = (
                await session.execute(
                    select(Jurisdiction).where(Jurisdiction.pm_jurisdiction_id == as_ulid(pm_jur))
                )
            ).scalar_one_or_none()
            return jur.id if jur is not None else None
        return None

    async def _local_parent_id(self, session: Any, record: dict) -> Any | None:
        """Resolve PM ``parent_id`` to a locally-anchored parent org id (best-effort)."""
        pm_parent = record.get("parent_id")
        if not pm_parent:
            return None
        parent = (
            await session.execute(
                select(Organization).where(Organization.pm_organization_id == as_ulid(pm_parent))
            )
        ).scalar_one_or_none()
        return parent.id if parent is not None else None

    def last_updated(self, obj: Any) -> datetime | None:
        if isinstance(obj, Organization):
            return obj.updated_at
        return parse_pm_timestamp(obj.get("updated_at"))
