"""Jurisdiction cache-mirror tests (Phase 1 — models only).

Exercises the four cache tables that mirror Power Map's jurisdiction extension
(see ``docs/specs/2026-05-31-jurisdictional-ia-design.md``):

- ``JurisdictionType`` — type lookup (16 rows seeded by migration to match PM).
- ``JurisdictionRelationshipType`` — relationship-type lookup (11 codes; carries
  ``is_symmetric`` + ``category``).
- ``Jurisdiction`` — entity row with ``type_id`` FK and bitemporal columns
  (``valid_from`` / ``valid_until`` / ``recorded_at`` / ``superseded_at``).
- ``JurisdictionRelationship`` — bitemporal junction over (subject, object, type).

These tests don't cover seeding (the migration in plan step 4 owns that), nor
the sidecar sync flow (separate follow-up plan). They verify the SQLAlchemy
mappings, FK wiring, and bitemporal-column round-trip behavior.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from clearinghouse_core.jurisdictions import (
    Jurisdiction,
    JurisdictionRelationship,
    JurisdictionRelationshipType,
    JurisdictionType,
)


@pytest.fixture
async def state_type(db_session) -> JurisdictionType:
    """A ``state`` JurisdictionType row used by jurisdiction-creating tests."""
    row = JurisdictionType(slug="state", display_name="State")
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def contained_by(db_session) -> JurisdictionRelationshipType:
    """The ``is_fully_contained_by`` spatial relationship type (directed)."""
    row = JurisdictionRelationshipType(
        code="is_fully_contained_by",
        display_name="Is fully contained by",
        category="spatial",
        is_symmetric=False,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def test_jurisdiction_type_round_trip(db_session):
    """JurisdictionType persists with slug + display_name + auto-generated ULID."""
    row = JurisdictionType(slug="county", display_name="County")
    db_session.add(row)
    await db_session.flush()

    result = await db_session.execute(
        select(JurisdictionType).where(JurisdictionType.slug == "county")
    )
    fetched = result.scalar_one()
    assert isinstance(fetched.id, ULID)
    assert fetched.slug == "county"
    assert fetched.display_name == "County"


async def test_jurisdiction_relationship_type_carries_symmetric_and_category(db_session):
    """``is_symmetric`` and ``category`` columns round-trip; ``is_symmetric`` defaults to False."""
    directed = JurisdictionRelationshipType(
        code="has_regulatory_authority_over",
        display_name="Has regulatory authority over",
        category="governance",
        is_symmetric=False,
    )
    symmetric = JurisdictionRelationshipType(
        code="is_coterminous_with",
        display_name="Is coterminous with",
        category="spatial",
        is_symmetric=True,
    )
    db_session.add_all([directed, symmetric])
    await db_session.flush()

    rows = (
        (
            await db_session.execute(
                select(JurisdictionRelationshipType).order_by(JurisdictionRelationshipType.code)
            )
        )
        .scalars()
        .all()
    )
    by_code = {r.code: r for r in rows}
    assert by_code["has_regulatory_authority_over"].is_symmetric is False
    assert by_code["has_regulatory_authority_over"].category == "governance"
    assert by_code["is_coterminous_with"].is_symmetric is True
    assert by_code["is_coterminous_with"].category == "spatial"


async def test_jurisdiction_with_type_fk_and_bitemporal_columns(db_session, state_type):
    """A Jurisdiction row carries ``type_id`` FK and the four bitemporal columns."""
    now = datetime.now(UTC)
    row = Jurisdiction(
        slug="usa-wa",
        name="Washington State",
        type_id=state_type.id,
        valid_from=datetime(1889, 11, 11, tzinfo=UTC),
        recorded_at=now,
    )
    db_session.add(row)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-wa"))
    ).scalar_one()
    assert isinstance(fetched.id, ULID)
    assert fetched.type_id == state_type.id
    assert fetched.valid_from == datetime(1889, 11, 11, tzinfo=UTC)
    assert fetched.valid_until is None
    assert fetched.recorded_at == now
    assert fetched.superseded_at is None
    assert fetched.pm_jurisdiction_id is None


async def test_jurisdiction_pm_jurisdiction_id_round_trip(db_session, state_type):
    """``pm_jurisdiction_id`` accepts a ULID for sidecar-synced rows."""
    pm_id = ULID()
    row = Jurisdiction(
        slug="usa",
        name="United States of America",
        type_id=state_type.id,
        recorded_at=datetime.now(UTC),
        pm_jurisdiction_id=pm_id,
    )
    db_session.add(row)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa"))
    ).scalar_one()
    assert fetched.pm_jurisdiction_id == pm_id


async def test_jurisdiction_relationship_bitemporal_round_trip(
    db_session, state_type, contained_by
):
    """A JurisdictionRelationship row joins subject + object with bitemporal columns."""
    country_type = JurisdictionType(slug="country", display_name="Country")
    db_session.add(country_type)
    await db_session.flush()

    usa = Jurisdiction(
        slug="usa", name="USA", type_id=country_type.id, recorded_at=datetime.now(UTC)
    )
    wa = Jurisdiction(
        slug="usa-wa", name="WA", type_id=state_type.id, recorded_at=datetime.now(UTC)
    )
    db_session.add_all([usa, wa])
    await db_session.flush()

    valid_from = datetime(1889, 11, 11, tzinfo=UTC)
    recorded_at = datetime.now(UTC)
    rel = JurisdictionRelationship(
        subject_jurisdiction_id=wa.id,
        object_jurisdiction_id=usa.id,
        relationship_type_id=contained_by.id,
        valid_from=valid_from,
        recorded_at=recorded_at,
        rel_metadata={"basis": "statehood"},
    )
    db_session.add(rel)
    await db_session.flush()

    fetched = (
        await db_session.execute(
            select(JurisdictionRelationship).where(
                JurisdictionRelationship.subject_jurisdiction_id == wa.id
            )
        )
    ).scalar_one()
    assert fetched.subject_jurisdiction_id == wa.id
    assert fetched.object_jurisdiction_id == usa.id
    assert fetched.relationship_type_id == contained_by.id
    assert fetched.valid_from == valid_from
    assert fetched.valid_until is None
    assert fetched.recorded_at == recorded_at
    assert fetched.superseded_at is None
    assert fetched.rel_metadata == {"basis": "statehood"}
    assert fetched.pm_relationship_id is None


async def test_jurisdiction_relationship_null_valid_from_uniqueness(
    db_session, state_type, contained_by
):
    """The partial unique index blocks duplicate (s, o, rt) rows with NULL ``valid_from``.

    PostgreSQL treats NULL as distinct in UNIQUE constraints by default, so the
    primary natural-key UNIQUE (which includes ``valid_from``) does not catch
    this case. The partial index
    ``uq_jurisdiction_relationships_natural_key_null_from`` closes the gap.
    """
    country_type = JurisdictionType(slug="country", display_name="Country")
    db_session.add(country_type)
    await db_session.flush()

    usa = Jurisdiction(
        slug="usa", name="USA", type_id=country_type.id, recorded_at=datetime.now(UTC)
    )
    wa = Jurisdiction(
        slug="usa-wa", name="WA", type_id=state_type.id, recorded_at=datetime.now(UTC)
    )
    db_session.add_all([usa, wa])
    await db_session.flush()

    db_session.add(
        JurisdictionRelationship(
            subject_jurisdiction_id=wa.id,
            object_jurisdiction_id=usa.id,
            relationship_type_id=contained_by.id,
            recorded_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    db_session.add(
        JurisdictionRelationship(
            subject_jurisdiction_id=wa.id,
            object_jurisdiction_id=usa.id,
            relationship_type_id=contained_by.id,
            recorded_at=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    # do not perform further session ops here — the failed flush invalidated the
    # outer savepoint; subsequent statements raise PendingRollbackError.
