"""Jurisdiction-model tests — the two tables #412 PR F kept.

- ``JurisdictionType`` — type lookup (16 rows seeded by migration).
- ``Jurisdiction`` — entity row with ``type_id`` FK and bitemporal columns
  (``valid_from`` / ``valid_until`` / ``recorded_at`` / ``superseded_at``).

Seeding is the migration's and ``usa_wa_common.seed_jurisdictions``' to test; these verify
the SQLAlchemy mappings, FK wiring, and bitemporal-column round-trip behavior.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType


@pytest.fixture
async def state_type(db_session) -> JurisdictionType:
    """A ``state`` JurisdictionType row used by jurisdiction-creating tests."""
    row = JurisdictionType(slug="state", display_name="State")
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
