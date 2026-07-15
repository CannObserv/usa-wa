"""Person descriptor — PM-first producer of legislators/officials.

Like organizations, PM has **backfilled people** (e.g. legislators) with curated
display names but **empty ``identifiers``**, so an identifier-keyed observation
cannot auto-attach — it would mint a duplicate. The same PM-first cascade applies:

1. **Identifier** — ``people/search?identifier_type=…&identifier_value=…`` (exact).
2. **Name** — ``people/search?q=<name>`` filters server-side via FTS (since
   power-map#201; ``pm_unaccent_simple`` also folds accents), so a single query
   suffices. The result is confirmed by an exact :func:`normalize_name` comparison,
   and a match is taken only when exactly one candidate remains — an ambiguous
   name (homonyms, no jurisdiction/hierarchy to disambiguate as orgs have) falls
   through to create-new rather than risk anchoring the wrong person.

Matched → adopt PM's display name + anchor; no PM write. New → observe-create.
Read is ``feed`` update-only (adopt PM's curated name; skip people we never
produced; ``local_match`` keys on the anchor).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Person, PersonIdentifier
from clearinghouse_sync_powermap.descriptors import (
    EntityDescriptor,
    as_ulid,
    normalize_name,
    parse_pm_timestamp,
)
from usa_wa_sync_powermap.descriptors.events import sync_entity_events

logger = get_logger(__name__)

#: PM search surface for people.
SEARCH_PATH = "/api/v1/people/search"
#: FTS candidate limit — and the **recall ceiling**: the exact match must rank within
#: it. Ample for a full name (FTS ANDs the name tokens → a small ranked set).
_SEARCH_LIMIT = 20


def identifier_type_for(source: str) -> str | None:
    """Map a local person's ``source`` to its PM ``identifier_type`` slug (design D1)."""
    if source == "usa_wa_legislature":
        return "person_wa_legislature_member_id"
    if source == "usa_wa_pdc":
        return "person_wa_pdc"
    return None


#: Local ``PersonIdentifier.scheme`` → PM ``identifier_type`` slug. Maps the child
#: identifier rows (the queryable N-scheme graph P1b built) to PM slugs so a cross-source
#: identifier — e.g. PDC's ``wa_pdc`` on a WSL-sourced Person (#69) — rides the person's
#: observation as an ``additional_identifier`` and attaches to the same PM person the
#: primary resolves. Schemes not here (or equal to the primary slug) are not emitted.
SCHEME_TO_IDENTIFIER_TYPE = {
    "wa_legislature_member_id": "person_wa_legislature_member_id",
    "wa_pdc": "person_wa_pdc",
}


class PersonDescriptor(EntityDescriptor):
    """Binds ``clearinghouse_domain_legislative.identity.Person`` to PM."""

    entity_type = "person"
    model = Person
    anchor_column = "pm_person_id"
    deleted_column = "deleted_at"  # terminal tombstone (#31); no id re-match yet → log-and-skip
    archived_column = "archived_at"  # PM reversible archival mirror (#41/#42)
    natural_key = ("source", "source_id")
    authority = "pm"
    read_path = "/api/v1/people"
    observe_path = "/api/v1/people/observations"
    read_source = "feed"
    # Cohort-only producer: feed is the primary read; the bounded anchored-cohort
    # backstop re-fetches only OUR anchored rows to recover dropped feed events (#13).
    reconcile_mode = "anchored_cohort"
    write_enabled = True
    enrich_identifier_type = "pm_person_id"  # enrich-on-match (#198)

    def __init__(self, *, search_match_cap: int | None = None) -> None:
        """``search_match_cap`` (#12): the name-match candidate window passed as the
        search ``limit``. ``None`` keeps the historical default (:data:`_SEARCH_LIMIT`);
        the registry plumbs an operator override from ``SidecarSettings``."""
        self.search_match_cap = _SEARCH_LIMIT if search_match_cap is None else search_match_cap

    async def needs_enrich(self, record: dict, row: Any) -> bool:
        """Enrich when PM's matched person lacks the identifier we hold for them."""
        id_type = identifier_type_for(row.source)
        return id_type is not None and not self.record_has_identifier(
            record, id_type, row.source_id
        )

    async def pm_match(self, client: Any, session: Any, row: Any) -> Any | None:
        # 1. Identifier — exact, server-side.
        id_type = identifier_type_for(row.source)
        if id_type is not None:
            page = await client.search_entities(
                SEARCH_PATH, identifier_type=id_type, identifier_value=row.source_id, limit=1
            )
            for rec in page.records:
                logger.info(
                    "person_pm_match_identifier",
                    extra={"source_id": row.source_id, "pm_id": rec.get("id")},
                )
                return as_ulid(rec["id"])

        # 2. Name — PM's q filters people server-side (FTS); confirm by exact normalized
        # match (see _SEARCH_LIMIT for the recall-ceiling note).
        target = normalize_name(row.name_full)
        page = await client.search_entities(
            SEARCH_PATH, q=row.name_full, limit=self.search_match_cap
        )
        named = [c for c in page.records if normalize_name(c.get("display_name") or "") == target]
        if len(named) == 1:
            logger.info(
                "person_pm_match_name",
                extra={"entity_name": row.name_full, "pm_id": named[0].get("id")},
            )
            return as_ulid(named[0]["id"])
        if len(named) > 1:
            logger.warning(
                "person_pm_match_ambiguous",
                extra={"entity_name": row.name_full, "candidates": [c.get("id") for c in named]},
            )
        return None  # genuinely new (or ambiguous) → observe-create

    async def to_observation(self, session: Any, row: Any) -> dict:
        id_type = identifier_type_for(row.source)
        if id_type is None:
            # Unknown source → no PM identifier_type; PM will reject. Surface it
            # (the outbox would otherwise read as a silent failure).
            logger.warning("person_identifier_type_unresolved", extra={"source": row.source})
        obs: dict[str, Any] = {
            "identifier_type": id_type,
            "identifier_value": row.source_id,
            # Typed name evidence — PM curates is_canonical; we never assert it.
            "names": [{"name": row.name_full, "name_type": "legal"}],
        }
        additional = await self._additional_identifiers(session, row, primary_slug=id_type)
        if additional:
            obs["additional_identifiers"] = additional
        return obs

    async def _additional_identifiers(
        self, session: Any, row: Any, *, primary_slug: str | None
    ) -> list[dict[str, str]]:
        """The person's child ``PersonIdentifier`` rows mapped to PM ``additional_identifiers``.

        Carries cross-source identifiers (e.g. PDC's ``wa_pdc`` on a WSL Person, #69) onto
        the same PM person the primary resolves. Skips a child whose scheme maps to the
        primary slug (already the top-level identifier) and any unmapped scheme.
        """
        rows = (
            await session.execute(
                select(PersonIdentifier)
                .where(PersonIdentifier.person_id == row.id)
                .order_by(PersonIdentifier.scheme)  # deterministic output order
            )
        ).scalars()
        seen: set[tuple[str, str]] = set()
        additional: list[dict[str, str]] = []
        for ident in rows:
            slug = SCHEME_TO_IDENTIFIER_TYPE.get(ident.scheme)
            if slug is None or slug == primary_slug:
                continue
            key = (slug, ident.value)
            if key in seen:
                continue
            seen.add(key)
            additional.append({"identifier_type_slug": slug, "identifier_value": ident.value})
        return additional

    async def local_match(self, session: Any, record: dict) -> Any | None:
        """Map a PM person to its local row by **anchor** (``pm_person_id``).

        Delegates to the tolerant base helper (usa-wa#86): a duplicate anchor logs
        + returns a deterministic winner rather than raising ``MultipleResultsFound``."""
        return await self._anchor_match(session, record)

    async def fetch_record(self, client: Any, pm_id: Any) -> dict | None:
        """Fetch the PM person and attach its ``/events`` sub-resource (#19).

        A parent feed bump may be an event change; embedding the events lets
        :meth:`upsert_from_pm` refresh the local ``entity_events`` mirror.
        """
        record = await client.get_entity(self.read_path, pm_id)
        if record is None:
            return None  # parent gone → skip the events fetch entirely
        record["events"] = await client.list_entity_events(self.read_path, pm_id)
        return record

    async def upsert_from_pm(self, session: Any, record: dict, existing: Any | None = None) -> Any:
        """Apply a PM person onto the local cache — **update-only** (see org descriptor)."""
        row = existing if existing is not None else await self.local_match(session, record)
        if row is None:
            return None
        name = record.get("display_name")
        if name:
            row.name_full = name  # adopt PM's curated display name
        if record.get("id") is not None:
            row.pm_person_id = as_ulid(record["id"])
        self.mirror_archival(row, record)  # PM archived_at → local archived_at mirror (#41/#42)
        await session.flush()
        if "events" in record:  # mirror the embedded events sub-resource (#19)
            await sync_entity_events(
                session, entity_kind="person", entity_id=row.id, pm_events=record["events"]
            )
        return row

    def last_updated(self, obj: Any) -> datetime | None:
        if isinstance(obj, Person):
            return obj.updated_at
        return parse_pm_timestamp(obj.get("updated_at"))
