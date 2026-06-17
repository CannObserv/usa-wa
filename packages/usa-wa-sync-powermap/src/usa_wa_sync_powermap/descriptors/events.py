"""Entity-event sub-resource sync (usa-wa#19).

Entity events are not a standalone entity — they are a sub-resource of person &
organization (spec step 6b). The person/org descriptors pull a parent's
``GET /{people|orgs}/{id}/events`` set and mirror it into
``canonical.entity_events``. This module owns the PM→local mapping and the
upsert/prune that keeps a parent's local event set in lockstep with PM.

Only the **read/mirror** direction is wired: usa-wa does not yet produce entity
events (no adapter writes the table), so there is no observation-embed path.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_domain_legislative.identity import EntityEvent
from clearinghouse_sync_powermap.descriptors import as_ulid

#: ``source`` stamped on every PM-originated event (natural key is ``(source, source_id)``;
#: ``source_id`` is PM's event id, so it equals ``pm_entity_event_id``).
EVENT_SOURCE = "powermap"


def map_pm_event(record: dict, *, entity_kind: str, entity_id: Any) -> dict:
    """Map a PM read ``EntityEvent`` dict onto local ``EntityEvent`` column values.

    Flattens the nested ``date`` (``PartialDate``) into ``event_year…event_second``
    and the ``event_type`` (``EventTypeInline``) onto ``event_type_slug`` — the slug
    is preferred and ``event_type_id`` left null to satisfy the slug-XOR-id
    constraint. ``entity_kind``/``entity_id`` come from the parent context (the local
    person/org row), not from PM.
    """
    date = record.get("date") or {}
    event_type = record.get("event_type") or {}
    # Both-or-neither (mirrors ck_entity_events_linked_entity_together): a PM
    # sub-record with only one half set is dropped wholesale rather than mapped to
    # a half-link that would raise IntegrityError and crash the parent upsert.
    linked_id = record.get("linked_entity_id")
    linked_kind = record.get("linked_entity_type")
    if not (linked_id and linked_kind):
        linked_id = linked_kind = None
    return {
        "source": EVENT_SOURCE,
        "source_id": record["id"],
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "event_type_slug": event_type.get("slug"),
        "event_type_id": None,  # slug preferred; XOR keeps id null
        "event_year": date.get("year"),
        "event_month": date.get("month"),
        "event_day": date.get("day"),
        "event_hour": date.get("hour"),
        "event_minute": date.get("minute"),
        "event_second": date.get("second"),
        "event_place_text": record.get("event_place_text"),
        "event_place_address": record.get("event_place_address"),
        "notes": record.get("notes"),
        "verified_at": record.get("verified_at"),
        "pm_created_at": record.get("created_at"),
        "visibility": record.get("visibility") or "public",
        "linked_entity_kind": linked_kind,
        "linked_entity_id": as_ulid(linked_id) if linked_id else None,
        "pm_entity_event_id": as_ulid(record["id"]),
    }


async def sync_entity_events(
    session: AsyncSession, *, entity_kind: str, entity_id: Any, pm_events: list[dict]
) -> None:
    """Reconcile a parent's local event mirror against PM's current event set.

    Insert events new to us (by ``pm_entity_event_id`` anchor), update existing
    rows in place, and prune locally-anchored rows that PM no longer reports for
    this parent. Touches only ``entity_events`` rows — never the parent — so it
    cannot trigger a spurious LWW write-back of the person/org.
    """
    existing = (
        (
            await session.execute(
                select(EntityEvent).where(
                    EntityEvent.entity_kind == entity_kind,
                    EntityEvent.entity_id == entity_id,
                )
            )
        )
        .scalars()
        .all()
    )
    by_anchor = {row.pm_entity_event_id: row for row in existing if row.pm_entity_event_id}

    seen: set[Any] = set()
    for record in pm_events:
        mapped = map_pm_event(record, entity_kind=entity_kind, entity_id=entity_id)
        anchor = mapped["pm_entity_event_id"]
        seen.add(anchor)
        row = by_anchor.get(anchor)
        if row is None:
            session.add(EntityEvent(**mapped))
        else:
            for column, value in mapped.items():
                setattr(row, column, value)

    for anchor, row in by_anchor.items():
        if anchor not in seen:
            await session.delete(row)

    await session.flush()
