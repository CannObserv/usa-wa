"""Schema-shape tests for the refined ``canonical.entity_events`` mirror.

``EntityEvent`` mirrors Power Map's ``ObservationEventItem`` (power-map#170):
granular partial dates, ``event_type_slug`` XOR ``event_type_id``, place text,
a constrained ``visibility``, and an optional polymorphic ``linked_entity``.
The old single ``date`` column + bare ``event_type`` string are gone — they
could not represent partial dates ("born 1970, month unknown").

Sub-resource sync wiring (person/org ``to_observation`` embedding ``events``,
``GET /{people|orgs}/{id}/events`` pulls) is deferred to a later batch; these
tests only assert the table shape + round-trips.
"""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from clearinghouse_domain_legislative.identity import EntityEvent, Person


def test_old_date_and_event_type_columns_removed():
    """The single ``date`` column + bare ``event_type`` string are gone."""
    cols = inspect(EntityEvent).columns
    assert "date" not in cols
    assert "event_type" not in cols


def test_granular_partial_date_columns_present_and_nullable():
    """Partial-date components mirror ObservationEventItem; each is nullable."""
    cols = inspect(EntityEvent).columns
    for name in (
        "event_year",
        "event_month",
        "event_day",
        "event_hour",
        "event_minute",
        "event_second",
    ):
        assert name in cols, name
        assert cols[name].nullable is True, name


def test_event_type_slug_and_id_columns_present_and_nullable():
    """Both ``event_type_slug`` and ``event_type_id`` exist, each nullable (XOR)."""
    cols = inspect(EntityEvent).columns
    assert cols["event_type_slug"].nullable is True
    assert cols["event_type_id"].nullable is True


def test_place_and_linked_entity_columns_present_and_nullable():
    """``event_place_text`` + optional ``linked_entity_*`` are nullable."""
    cols = inspect(EntityEvent).columns
    assert cols["event_place_text"].nullable is True
    assert cols["linked_entity_kind"].nullable is True
    assert cols["linked_entity_id"].nullable is True


def test_visibility_column_present_and_nullable():
    cols = inspect(EntityEvent).columns
    assert "visibility" in cols


def test_pm_mirror_columns_present_and_nullable():
    """The full-PM-mirror columns (#19) exist and are nullable."""
    cols = inspect(EntityEvent).columns
    for name in ("event_place_address", "notes", "verified_at", "pm_created_at"):
        assert name in cols, name
        assert cols[name].nullable is True, name


async def test_pm_mirror_columns_round_trip(db_session):
    """The JSONB address + text mirror fields persist and read back unchanged."""
    event = _base_event(
        source_id="evt-mirror",
        event_place_address={"id": "addr-1", "city": "Olympia", "region": "WA"},
        notes="curated by PM",
        verified_at="2026-06-01T00:00:00.000000Z",
        pm_created_at="2026-05-01T00:00:00.000000Z",
    )
    db_session.add(event)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(EntityEvent).where(EntityEvent.source_id == "evt-mirror"))
    ).scalar_one()
    assert fetched.event_place_address == {"id": "addr-1", "city": "Olympia", "region": "WA"}
    assert fetched.notes == "curated by PM"
    assert fetched.verified_at == "2026-06-01T00:00:00.000000Z"
    assert fetched.pm_created_at == "2026-05-01T00:00:00.000000Z"


def _base_event(**overrides) -> EntityEvent:
    """An EntityEvent with the minimal valid field set, plus overrides."""
    kwargs: dict = {
        "source": "pm",
        "source_id": "evt-x",
        "entity_kind": "person",
        "entity_id": ULID(),
        "event_type_slug": "birth",
        "visibility": "public",
    }
    kwargs.update(overrides)
    return EntityEvent(**kwargs)


async def test_partial_date_round_trip(db_session):
    """A "born 1970, month/day unknown" event persists with only the year set."""
    person = Person(source="wsl", source_id="p-pd", name_full="Partial Date")
    db_session.add(person)
    await db_session.flush()

    event = _base_event(
        source_id="evt-pd",
        entity_id=person.id,
        event_year=1970,
        event_month=None,
        event_day=None,
    )
    db_session.add(event)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(EntityEvent).where(EntityEvent.source_id == "evt-pd"))
    ).scalar_one()
    assert fetched.event_year == 1970
    assert fetched.event_month is None
    assert fetched.event_day is None
    assert fetched.event_type_slug == "birth"
    assert fetched.event_type_id is None
    assert fetched.visibility == "public"
    assert fetched.linked_entity_kind is None
    assert fetched.linked_entity_id is None
    assert fetched.pm_entity_event_id is None


async def test_full_timestamp_components_round_trip(db_session):
    event = _base_event(
        source_id="evt-full",
        event_year=2024,
        event_month=3,
        event_day=15,
        event_hour=13,
        event_minute=30,
        event_second=45,
        event_place_text="Olympia, WA",
    )
    db_session.add(event)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(EntityEvent).where(EntityEvent.source_id == "evt-full"))
    ).scalar_one()
    assert (fetched.event_hour, fetched.event_minute, fetched.event_second) == (13, 30, 45)
    assert fetched.event_place_text == "Olympia, WA"


async def test_event_type_id_alternative_round_trip(db_session):
    """An event may instead carry ``event_type_id`` (the XOR alternative)."""
    type_id = ULID()
    event = _base_event(
        source_id="evt-typeid",
        event_type_slug=None,
        event_type_id=type_id,
    )
    db_session.add(event)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(EntityEvent).where(EntityEvent.source_id == "evt-typeid"))
    ).scalar_one()
    assert fetched.event_type_id == type_id
    assert fetched.event_type_slug is None


async def test_linked_entity_round_trip(db_session):
    linked = ULID()
    event = _base_event(
        source_id="evt-linked",
        linked_entity_kind="organization",
        linked_entity_id=linked,
    )
    db_session.add(event)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(EntityEvent).where(EntityEvent.source_id == "evt-linked"))
    ).scalar_one()
    assert fetched.linked_entity_kind == "organization"
    assert fetched.linked_entity_id == linked


async def test_event_type_xor_rejects_both(db_session):
    """Supplying both ``event_type_slug`` and ``event_type_id`` violates the XOR."""
    event = _base_event(
        source_id="evt-both",
        event_type_slug="birth",
        event_type_id=ULID(),
    )
    db_session.add(event)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_event_type_xor_rejects_neither(db_session):
    """Supplying neither ``event_type_slug`` nor ``event_type_id`` violates the XOR."""
    event = _base_event(
        source_id="evt-neither",
        event_type_slug=None,
        event_type_id=None,
    )
    db_session.add(event)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_visibility_rejects_unknown_value(db_session):
    event = _base_event(source_id="evt-vis", visibility="secret")
    db_session.add(event)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.parametrize("vis", ["public", "legal_only", "hidden"])
async def test_visibility_accepts_allowed_values(db_session, vis):
    event = _base_event(source_id=f"evt-vis-{vis}", visibility=vis)
    db_session.add(event)
    await db_session.flush()
    assert event.id is not None


async def test_linked_entity_kind_rejects_unknown_value(db_session):
    event = _base_event(
        source_id="evt-lek",
        linked_entity_kind="alien",
        linked_entity_id=ULID(),
    )
    db_session.add(event)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_linked_entity_requires_both_or_neither(db_session):
    """``linked_entity_kind`` and ``linked_entity_id`` are set together or not at all."""
    event = _base_event(
        source_id="evt-lek-half",
        linked_entity_kind="person",
        linked_entity_id=None,
    )
    db_session.add(event)
    with pytest.raises(IntegrityError):
        await db_session.flush()
