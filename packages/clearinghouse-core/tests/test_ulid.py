"""Round-trip tests for the ``ULID`` SQLAlchemy column type."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType


@pytest.fixture
def usa_wa_id() -> ULID:
    return ULID()


@pytest.fixture
async def state_type(db_session) -> JurisdictionType:
    """A ``state`` JurisdictionType row used by all Jurisdiction-creating tests below."""
    row = JurisdictionType(slug="state", display_name="State")
    db_session.add(row)
    await db_session.flush()
    return row


async def test_ulid_round_trip(db_session, usa_wa_id, state_type):
    """Writing a ULID PK and reading it back yields an equal ULID instance."""
    row = Jurisdiction(
        id=usa_wa_id,
        slug="usa-wa",
        name="Washington State",
        type_id=state_type.id,
        recorded_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.flush()

    result = await db_session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-wa"))
    fetched = result.scalar_one()

    assert isinstance(fetched.id, ULID)
    assert fetched.id == usa_wa_id
    assert str(fetched.id) == str(usa_wa_id)


async def test_ulid_accepts_uuid_at_bind(db_session, state_type):
    """The TypeDecorator accepts a uuid.UUID at bind time and returns a ULID at read time."""
    raw_ulid = ULID()
    as_uuid = raw_ulid.to_uuid()
    assert isinstance(as_uuid, UUID)

    row = Jurisdiction(
        id=as_uuid,
        slug="usa-or",
        name="Oregon",
        type_id=state_type.id,
        recorded_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.flush()

    result = await db_session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-or"))
    fetched = result.scalar_one()

    assert isinstance(fetched.id, ULID)
    assert fetched.id == raw_ulid


async def test_ulid_time_ordering_preserved(db_session, state_type):
    """ULIDs minted a millisecond apart sort in time order — what B-tree locality relies on.

    The ULID prefix is a millisecond timestamp; inside one millisecond the sort order falls to
    the random suffix, so the two mints are separated by more than a tick (#205).
    """
    earlier = ULID()
    await asyncio.sleep(0.002)
    later = ULID()
    assert earlier.timestamp < later.timestamp
    assert earlier < later

    now = datetime.now(UTC)
    db_session.add_all(
        [
            Jurisdiction(
                id=earlier,
                slug="earlier",
                name="Earlier",
                type_id=state_type.id,
                recorded_at=now,
            ),
            Jurisdiction(
                id=later,
                slug="later",
                name="Later",
                type_id=state_type.id,
                recorded_at=now,
            ),
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
