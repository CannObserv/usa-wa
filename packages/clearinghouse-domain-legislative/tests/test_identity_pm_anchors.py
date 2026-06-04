"""PM-anchor + entity-events tests for the identity cluster (sidecar step 1).

Covers the schema-wide ``pm_<entity>_id`` standardization and the new
``canonical.entity_events`` mirror table. The sync flow itself is exercised in
``clearinghouse-sync-powermap``; here we only verify the mappings + round-trips.
"""

from datetime import date

from sqlalchemy import inspect, select
from ulid import ULID

from clearinghouse_domain_legislative.identity import (
    Assignment,
    EntityEvent,
    Organization,
    Person,
    Role,
)


def test_pm_anchor_columns_renamed_and_added():
    """Anchors follow ``pm_<entity>_id``; the old ``powermap_*`` names are gone."""
    assert "pm_person_id" in inspect(Person).columns
    assert "powermap_person_id" not in inspect(Person).columns
    assert "pm_organization_id" in inspect(Organization).columns
    assert "powermap_organization_id" not in inspect(Organization).columns
    assert "pm_role_id" in inspect(Role).columns
    assert "pm_assignment_id" in inspect(Assignment).columns


async def test_person_pm_anchor_round_trip(db_session, usa_wa):
    """``pm_person_id`` accepts a ULID for sidecar-synced rows and defaults null."""
    pm_id = ULID()
    person = Person(
        jurisdiction_id=usa_wa.id,
        source="wsl",
        source_id="p-1",
        name_full="Jane Doe",
        pm_person_id=pm_id,
    )
    db_session.add(person)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(Person).where(Person.source_id == "p-1"))
    ).scalar_one()
    assert fetched.pm_person_id == pm_id


async def test_entity_event_round_trip(db_session, usa_wa):
    """An entity event mirrors PM's polymorphic lifecycle row."""
    person = Person(jurisdiction_id=usa_wa.id, source="wsl", source_id="p-2", name_full="John Roe")
    db_session.add(person)
    await db_session.flush()

    event = EntityEvent(
        jurisdiction_id=usa_wa.id,
        source="pm",
        source_id="evt-1",
        entity_kind="person",
        entity_id=person.id,
        event_type="birth",
        date=date(1970, 1, 1),
    )
    db_session.add(event)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(EntityEvent).where(EntityEvent.source_id == "evt-1"))
    ).scalar_one()
    assert fetched.entity_kind == "person"
    assert fetched.entity_id == person.id
    assert fetched.event_type == "birth"
    assert fetched.date == date(1970, 1, 1)
    assert fetched.pm_entity_event_id is None
