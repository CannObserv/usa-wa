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
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid, normalize_name
from usa_wa_sync_powermap.descriptors.events import sync_entity_events

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


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class OrganizationDescriptor(EntityDescriptor):
    """Binds ``clearinghouse_domain_legislative.identity.Organization`` to PM."""

    entity_type = "organization"
    model = Organization
    anchor_column = "pm_organization_id"
    # Local idempotency is on the natural key, but PM-record → local-row mapping
    # keys on the anchor (PM's backfilled orgs have no usa-wa identifier to derive
    # ``source``/``source_id`` from) — see :meth:`local_match`.
    natural_key = ("source", "source_id")
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
        # fallback, so a single query suffices.
        target = normalize_name(row.name)
        page = await client.search_entities(
            SEARCH_PATH, q=row.name, jurisdiction=JURISDICTION_SLUG, limit=self.search_match_cap
        )
        named = [c for c in page.records if normalize_name(c.get("name") or "") == target]
        if len(named) == 1:
            logger.info(
                "org_pm_match_name", extra={"entity_name": row.name, "pm_id": named[0].get("id")}
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
                        extra={"entity_name": row.name, "pm_id": scoped[0].get("id")},
                    )
                    return as_ulid(scoped[0]["id"])
            logger.warning(
                "org_pm_match_ambiguous",
                extra={"entity_name": row.name, "candidates": [c.get("id") for c in named]},
            )

        return None  # genuinely new → observe-create

    async def _parent_pm_id(self, session: Any, row: Any) -> Any | None:
        if row.parent_organization_id is None:
            return None
        parent = await session.get(Organization, row.parent_organization_id)
        return parent.pm_organization_id if parent is not None else None

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
            "names": [{"name": row.name, "name_type": "legal"}],
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
        await session.flush()
        if "events" in record:  # mirror the embedded events sub-resource (#19)
            await sync_entity_events(
                session, entity_kind="organization", entity_id=row.id, pm_events=record["events"]
            )
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
        ts = obj.get("updated_at")
        return _parse_ts(ts) if ts else None
