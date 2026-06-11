"""Person descriptor — PM-first producer of legislators/officials.

Like organizations, PM has **backfilled people** (e.g. legislators) with curated
display names but **empty ``identifiers``**, so an identifier-keyed observation
cannot auto-attach — it would mint a duplicate. The same PM-first cascade applies:

1. **Identifier** — ``people/search?identifier_type=…&identifier_value=…`` (exact).
2. **Name** — ``people/search?q=<name>``; PM's ``q`` *does* filter people by name
   server-side (it does not for orgs), so no cohort enumeration is needed. The
   server result is then confirmed by an exact :func:`normalize_name` comparison,
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
from clearinghouse_domain_legislative.identity import Person
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, as_ulid, normalize_name

logger = get_logger(__name__)

#: PM search surface for people.
SEARCH_PATH = "/api/v1/people/search"


def identifier_type_for(source: str) -> str | None:
    """Map a local person's ``source`` to its PM ``identifier_type`` slug (design D1)."""
    if source == "usa_wa_legislature":
        return "person_wa_legislature_member_id"
    if source == "usa_wa_pdc":
        return "person_wa_pdc"
    return None


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class PersonDescriptor(EntityDescriptor):
    """Binds ``clearinghouse_domain_legislative.identity.Person`` to PM."""

    entity_type = "person"
    model = Person
    anchor_column = "pm_person_id"
    natural_key = ("source", "source_id")
    authority = "pm"
    read_path = "/api/v1/people"
    observe_path = "/api/v1/people/observations"
    read_source = "feed"
    reconcile_enabled = False  # cohort-only producer; feed is the only read (see #13)
    write_enabled = True
    enrich_identifier_type = "pm_person_id"  # enrich-on-match (#198)

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

        # 2. Name — PM's q filters people server-side; confirm by exact normalized match.
        target = normalize_name(row.name_full)
        page = await client.search_entities(SEARCH_PATH, q=row.name_full, limit=20)
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
        return {
            "identifier_type": id_type,
            "identifier_value": row.source_id,
            # Typed name evidence — PM curates is_canonical; we never assert it.
            "names": [{"name": row.name_full, "name_type": "legal"}],
        }

    async def local_match(self, session: Any, record: dict) -> Any | None:
        """Map a PM person to its local row by **anchor** (``pm_person_id``)."""
        pm_id = record.get("id")
        if pm_id is None:
            return None
        return (
            await session.execute(select(Person).where(Person.pm_person_id == as_ulid(pm_id)))
        ).scalar_one_or_none()

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
        await session.flush()
        return row

    def last_updated(self, obj: Any) -> datetime | None:
        if isinstance(obj, Person):
            return obj.updated_at
        ts = obj.get("updated_at")
        return _parse_ts(ts) if ts else None
