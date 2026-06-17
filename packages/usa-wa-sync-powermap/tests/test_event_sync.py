"""Entity-event sync helper tests (usa-wa#19).

The person/org descriptors mirror PM's read ``EntityEvent`` sub-resource into
``canonical.entity_events``. These tests cover the pure PM→local mapping
(nested ``PartialDate``/``EventTypeInline`` → flat columns, full-mirror fields,
natural key) and the upsert/prune behaviour against a parent's event set.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.identity import EntityEvent, Organization, Person
from clearinghouse_sync_powermap.engine import APPLY_KEPT_LOCAL, APPLY_UPDATED, SyncEngine
from clearinghouse_sync_powermap.testing import FakeClient
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor, PersonDescriptor
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


def test_map_pm_event_drops_partial_link():
    """A link with only one half set is dropped wholesale (both-or-neither CHECK):
    a malformed PM sub-record must not crash the parent upsert."""
    id_only = map_pm_event(
        _pm_event(str(ULID()), linked_entity_id=str(ULID())),  # id without type
        entity_kind="person",
        entity_id=ULID(),
    )
    assert id_only["linked_entity_kind"] is None
    assert id_only["linked_entity_id"] is None

    type_only = map_pm_event(
        _pm_event(str(ULID()), linked_entity_type="organization"),  # type without id
        entity_kind="person",
        entity_id=ULID(),
    )
    assert type_only["linked_entity_kind"] is None
    assert type_only["linked_entity_id"] is None


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


# --- descriptor sub-resource wiring ----------------------------------------


async def test_person_fetch_record_attaches_events():
    pm_id = ULID()
    eid = str(ULID())
    client = FakeClient(
        entities={pm_id: {"id": str(pm_id), "display_name": "Jane"}},
        events={pm_id: [_pm_event(eid)]},
    )
    record = await PersonDescriptor().fetch_record(client, pm_id)
    assert record["display_name"] == "Jane"
    assert [e["id"] for e in record["events"]] == [eid]
    assert client.events_fetched == [("/api/v1/people", pm_id)]


async def test_org_fetch_record_attaches_events():
    pm_id = ULID()
    eid = str(ULID())
    client = FakeClient(
        entities={pm_id: {"id": str(pm_id), "name": "Acme"}},
        events={pm_id: [_pm_event(eid, slug="founding")]},
    )
    record = await OrganizationDescriptor().fetch_record(client, pm_id)
    assert [e["id"] for e in record["events"]] == [eid]
    assert client.events_fetched == [("/api/v1/orgs", pm_id)]


async def test_fetch_record_skips_events_when_parent_gone():
    pm_id = ULID()
    client = FakeClient(entities={})  # get_entity → None (404 / deleted)
    assert await PersonDescriptor().fetch_record(client, pm_id) is None
    assert client.events_fetched == []  # no events fetch for a vanished parent


async def test_person_upsert_mirrors_embedded_events(db_session):
    pm_id = ULID()
    person = Person(
        source="usa_wa_legislature",
        source_id="L1",
        name_full="Jane",
        pm_person_id=pm_id,
    )
    db_session.add(person)
    await db_session.flush()

    eid = str(ULID())
    record = {"id": str(pm_id), "display_name": "Jane Doe", "events": [_pm_event(eid)]}
    await PersonDescriptor().upsert_from_pm(db_session, record)

    rows = await _events_for(db_session, person.id)
    assert [r.source_id for r in rows] == [eid]
    assert rows[0].entity_kind == "person"


async def test_org_upsert_mirrors_embedded_events(db_session):
    pm_id = ULID()
    org = Organization(
        source="usa_wa_legislature",
        source_id="C1",
        name="Acme",
        org_type="committee",
        pm_organization_id=pm_id,
    )
    db_session.add(org)
    await db_session.flush()

    eid = str(ULID())
    record = {"id": str(pm_id), "name": "Acme Corp", "events": [_pm_event(eid, slug="founding")]}
    await OrganizationDescriptor().upsert_from_pm(db_session, record)

    rows = await _events_for(db_session, org.id)
    assert [r.source_id for r in rows] == [eid]
    assert rows[0].entity_kind == "organization"


# --- engine end-to-end (LWW gate governs event mirroring) -------------------


async def test_engine_apply_record_mirrors_events_when_pm_newer(db_session):
    """PM clock newer (a parent-propagation bump from an event change) → PM wins,
    upsert runs, and the embedded events are mirrored."""
    pm_id = ULID()
    person = Person(
        source="usa_wa_legislature", source_id="L9", name_full="Jane", pm_person_id=pm_id
    )
    person.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    db_session.add(person)
    await db_session.flush()

    eid = str(ULID())
    record = {
        "id": str(pm_id),
        "display_name": "Jane",
        "updated_at": "2026-06-01T00:00:00Z",  # newer than local
        "events": [_pm_event(eid)],
    }
    engine = SyncEngine([PersonDescriptor()], FakeClient())
    outcome = await engine.apply_record(db_session, PersonDescriptor(), record)

    assert outcome == APPLY_UPDATED
    rows = await _events_for(db_session, person.id)
    assert [r.source_id for r in rows] == [eid]


async def test_engine_keeps_local_skips_event_mirror_when_local_newer(db_session):
    """Local clock strictly newer → KEPT_LOCAL: upsert (and thus event mirroring)
    is skipped. Safe because PM bumps the parent clock whenever an event changes,
    so a stale parent clock means events did not change."""
    pm_id = ULID()
    person = Person(
        source="usa_wa_legislature", source_id="L8", name_full="Jane", pm_person_id=pm_id
    )
    person.updated_at = datetime(2026, 6, 1, tzinfo=UTC)
    db_session.add(person)
    await db_session.flush()

    record = {
        "id": str(pm_id),
        "display_name": "Jane",
        "updated_at": "2026-01-01T00:00:00Z",  # older than local
        "events": [_pm_event(str(ULID()))],
    }
    engine = SyncEngine([PersonDescriptor()], FakeClient())
    outcome = await engine.apply_record(db_session, PersonDescriptor(), record)

    assert outcome == APPLY_KEPT_LOCAL
    assert await _events_for(db_session, person.id) == []
