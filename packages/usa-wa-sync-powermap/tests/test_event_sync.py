"""Entity-event sync helper tests (usa-wa#19).

The person/org descriptors mirror PM's read ``EntityEvent`` sub-resource into
``canonical.entity_events``. These tests cover the pure PM→local mapping
(nested ``PartialDate``/``EventTypeInline`` → flat columns, full-mirror fields,
natural key) and the upsert/prune behaviour against a parent's event set.
"""

from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.identity import EntityEvent, Person
from usa_wa_sync_powermap.descriptors.events import (
    EVENT_SOURCE,
    map_pm_event,
    sync_entity_events,
)


def _pm_event(event_id: str, *, slug="birth", date=None, **extra) -> dict:
    """A PM read EntityEvent dict (as produced by the generated model's to_dict)."""
    rec = {
        "id": event_id,
        "event_type": {"id": str(ULID()), "slug": slug, "display_name": slug.title()},
        "date": {"year": 1970} if date is None else date,
        "visibility": "public",
        "created_at": "2026-05-01T00:00:00Z",
    }
    rec.update(extra)
    return rec


def test_map_pm_event_partial_date_and_type_slug_preferred():
    eid = str(ULID())
    entity_id = ULID()
    mapped = map_pm_event(_pm_event(eid), entity_kind="person", entity_id=entity_id)

    assert mapped["source"] == EVENT_SOURCE == "powermap"
    assert mapped["source_id"] == eid
    assert mapped["pm_entity_event_id"] == ULID.from_str(eid)
    assert mapped["entity_kind"] == "person"
    assert mapped["entity_id"] == entity_id
    # slug preferred; id left null to satisfy the XOR.
    assert mapped["event_type_slug"] == "birth"
    assert mapped["event_type_id"] is None
    assert mapped["event_year"] == 1970
    assert mapped["event_month"] is None
    assert mapped["visibility"] == "public"


def test_map_pm_event_full_timestamp_and_mirror_fields():
    mapped = map_pm_event(
        _pm_event(
            str(ULID()),
            date={"year": 2024, "month": 3, "day": 15, "hour": 13, "minute": 30, "second": 45},
            event_place_text="Olympia, WA",
            event_place_address={"id": "addr-1", "city": "Olympia", "region": "WA"},
            notes="curated by PM",
            verified_at="2026-06-01T00:00:00Z",
        ),
        entity_kind="organization",
        entity_id=ULID(),
    )
    assert (mapped["event_hour"], mapped["event_minute"], mapped["event_second"]) == (13, 30, 45)
    assert mapped["event_place_text"] == "Olympia, WA"
    assert mapped["event_place_address"] == {"id": "addr-1", "city": "Olympia", "region": "WA"}
    assert mapped["notes"] == "curated by PM"
    assert mapped["verified_at"] == "2026-06-01T00:00:00Z"
    assert mapped["pm_created_at"] == "2026-05-01T00:00:00Z"


def test_map_pm_event_linked_entity_present_and_absent():
    linked = ULID()
    with_link = map_pm_event(
        _pm_event(str(ULID()), linked_entity_type="organization", linked_entity_id=str(linked)),
        entity_kind="person",
        entity_id=ULID(),
    )
    assert with_link["linked_entity_kind"] == "organization"
    assert with_link["linked_entity_id"] == linked

    without = map_pm_event(_pm_event(str(ULID())), entity_kind="person", entity_id=ULID())
    assert without["linked_entity_kind"] is None
    assert without["linked_entity_id"] is None


async def _add_person(session) -> Person:
    p = Person(source="wsl", source_id="p-evt", name_full="Event Subject")
    session.add(p)
    await session.flush()
    return p


async def _events_for(session, entity_id) -> list[EntityEvent]:
    return list(
        (await session.execute(select(EntityEvent).where(EntityEvent.entity_id == entity_id)))
        .scalars()
        .all()
    )


async def test_sync_inserts_new_events(db_session):
    person = await _add_person(db_session)
    a, b = str(ULID()), str(ULID())
    await sync_entity_events(
        db_session,
        entity_kind="person",
        entity_id=person.id,
        pm_events=[_pm_event(a), _pm_event(b, slug="death")],
    )
    rows = await _events_for(db_session, person.id)
    assert {r.source_id for r in rows} == {a, b}
    assert all(r.source == "powermap" for r in rows)
    assert all(r.pm_entity_event_id is not None for r in rows)


async def test_sync_updates_existing_by_anchor(db_session):
    person = await _add_person(db_session)
    eid = str(ULID())
    await sync_entity_events(
        db_session, entity_kind="person", entity_id=person.id, pm_events=[_pm_event(eid)]
    )
    # Re-sync the same anchor with a changed visibility → update in place, no dup.
    await sync_entity_events(
        db_session,
        entity_kind="person",
        entity_id=person.id,
        pm_events=[_pm_event(eid, visibility="legal_only")],
    )
    rows = await _events_for(db_session, person.id)
    assert len(rows) == 1
    assert rows[0].visibility == "legal_only"


async def test_sync_prunes_events_absent_from_pm(db_session):
    person = await _add_person(db_session)
    keep, drop = str(ULID()), str(ULID())
    await sync_entity_events(
        db_session,
        entity_kind="person",
        entity_id=person.id,
        pm_events=[_pm_event(keep), _pm_event(drop)],
    )
    # PM now reports only `keep` → `drop` is pruned locally.
    await sync_entity_events(
        db_session, entity_kind="person", entity_id=person.id, pm_events=[_pm_event(keep)]
    )
    rows = await _events_for(db_session, person.id)
    assert {r.source_id for r in rows} == {keep}
