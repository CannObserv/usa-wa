"""PM-anchor + entity-events tests for the identity cluster (sidecar step 1).

Covers the schema-wide ``pm_<entity>_id`` standardization and the new
``canonical.entity_events`` mirror table. The sync flow itself is exercised in
``clearinghouse-sync-powermap``; here we only verify the mappings + round-trips.
"""

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


async def test_person_pm_anchor_round_trip(db_session):
    """``pm_person_id`` accepts a ULID for sidecar-synced rows and defaults null.

    People carry no ``jurisdiction_id`` (decoupling, 2026-06-09)."""
    pm_id = ULID()
    person = Person(
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


async def test_entity_event_round_trip(db_session):
    """An entity event mirrors PM's ObservationEventItem shape (no jurisdiction)."""
    person = Person(source="wsl", source_id="p-2", name_full="John Roe")
    db_session.add(person)
    await db_session.flush()

    event = EntityEvent(
        source="pm",
        source_id="evt-1",
        entity_kind="person",
        entity_id=person.id,
        event_type_slug="birth",
        event_year=1970,
        event_month=1,
        event_day=1,
        visibility="public",
    )
    db_session.add(event)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(EntityEvent).where(EntityEvent.source_id == "evt-1"))
    ).scalar_one()
    assert fetched.entity_kind == "person"
    assert fetched.entity_id == person.id
    assert fetched.event_type_slug == "birth"
    assert (fetched.event_year, fetched.event_month, fetched.event_day) == (1970, 1, 1)
    assert fetched.pm_entity_event_id is None
