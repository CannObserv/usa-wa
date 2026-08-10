"""PM-anchor + entity-events tests for the identity cluster (sidecar step 1).

Covers the schema-wide ``pm_<entity>_id`` standardization and the new
``canonical.entity_events`` mirror table. The sync flow itself is exercised in
``clearinghouse-sync-powermap``; here we only verify the mappings + round-trips.
"""

from pathlib import Path

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


# --- CR #196 finding 40: the span-kind filter must be index-seekable ----------


def test_the_span_kind_expression_index_is_declared():
    """``/api/v1/assignments?span_kind=`` filters on ``split_part(source_id, ':', 2)``.

    Without an index on that expression every filtered page is a Seq Scan **plus a
    Sort**, which voids the premise the route's keyset pagination is built on
    ("index-seekable at any depth"). Measured on a 200k-row probe: Seq Scan+Sort →
    Index Scan, no sort.

    The trailing ``id`` column is what lets the same index satisfy the route's
    ``ORDER BY id`` — dropping it would leave the scan indexed but the sort intact, so
    the assertion checks position, not just presence.
    """
    index = next(
        (ix for ix in Assignment.__table__.indexes if ix.name == "ix_assignments_span_kind"),
        None,
    )
    assert index is not None, "the span-kind expression index is gone"

    rendered = [str(expr) for expr in index.expressions]
    assert any("split_part" in expr for expr in rendered), rendered
    assert rendered[-1].endswith("id"), f"id must trail so ORDER BY id is served: {rendered}"


def test_the_span_kind_index_matches_the_migrations_copy():
    """``alembic/versions/`` cannot import this module without coupling a historical
    migration to a live one, so the migration keeps its own literal — the same seam
    ``job_runs`` and ``source_coverage`` carry for their CHECK constraints."""
    migration = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "0598c2e839ef_196_index_span_kind.py"
    ).read_text()
    assert "ix_assignments_span_kind" in migration
    assert "split_part(source_id, ':', 2)" in migration
