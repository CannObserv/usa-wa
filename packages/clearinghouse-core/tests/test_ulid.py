"""Round-trip tests for the ``ULID`` SQLAlchemy column type."""

from uuid import UUID

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_core.provenance import Jurisdiction, JurisdictionLevel


@pytest.fixture
def usa_wa_id() -> ULID:
    return ULID()


async def test_ulid_round_trip(db_session, usa_wa_id):
    """Writing a ULID PK and reading it back yields an equal ULID instance."""
    row = Jurisdiction(
        id=usa_wa_id,
        slug="usa-wa",
        name="Washington State",
        level=JurisdictionLevel.state,
    )
    db_session.add(row)
    await db_session.flush()

    result = await db_session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-wa"))
    fetched = result.scalar_one()

    assert isinstance(fetched.id, ULID)
    assert fetched.id == usa_wa_id
    assert str(fetched.id) == str(usa_wa_id)


async def test_ulid_accepts_uuid_at_bind(db_session):
    """The TypeDecorator accepts a uuid.UUID at bind time and returns a ULID at read time."""
    raw_ulid = ULID()
    as_uuid = raw_ulid.to_uuid()
    assert isinstance(as_uuid, UUID)

    row = Jurisdiction(
        id=as_uuid,
        slug="usa-or",
        name="Oregon",
        level=JurisdictionLevel.state,
    )
    db_session.add(row)
    await db_session.flush()

    result = await db_session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-or"))
    fetched = result.scalar_one()

    assert isinstance(fetched.id, ULID)
    assert fetched.id == raw_ulid


async def test_ulid_time_ordering_preserved(db_session):
    """ULIDs created later sort after earlier ones — the property we rely on for B-tree locality."""
    earlier = ULID()
    later = ULID()
    assert earlier < later

    db_session.add_all(
        [
            Jurisdiction(id=earlier, slug="earlier", name="Earlier", level=JurisdictionLevel.state),
            Jurisdiction(id=later, slug="later", name="Later", level=JurisdictionLevel.state),
        ]
    )
    await db_session.flush()

    result = await db_session.execute(
        select(Jurisdiction)
        .where(Jurisdiction.slug.in_(["earlier", "later"]))
        .order_by(Jurisdiction.id)
    )
    rows = result.scalars().all()
    assert [r.slug for r in rows] == ["earlier", "later"]
