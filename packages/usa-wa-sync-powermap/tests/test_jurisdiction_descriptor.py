"""JurisdictionDescriptor tests — observation payload + PM-record upsert (step 6)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from usa_wa_sync_powermap.descriptors import JurisdictionDescriptor


@pytest.fixture
def descriptor() -> JurisdictionDescriptor:
    return JurisdictionDescriptor()


@pytest.fixture
async def state_type(db_session) -> JurisdictionType:
    jt = JurisdictionType(slug="state", display_name="State")
    db_session.add(jt)
    await db_session.flush()
    return jt


def _pm_record(slug="usa-wa", name="Washington", *, pm_id=None, updated_at="2026-06-06T00:00:00Z"):
    return {
        "id": str(pm_id or ULID()),
        "slug": slug,
        "name": name,
        "type": {"id": str(ULID()), "slug": "state", "display_name": "State"},
        "recorded_at": "2022-01-01T00:00:00Z",
        "valid_from": "2022-01-01T00:00:00Z",
        "valid_until": None,
        "superseded_at": None,
        "updated_at": updated_at,
    }


async def test_to_observation_keys_on_jur_slug(db_session, descriptor, state_type):
    row = Jurisdiction(
        slug="usa-wa", name="Washington", type_id=state_type.id, recorded_at=datetime.now(UTC)
    )
    db_session.add(row)
    await db_session.flush()

    obs = await descriptor.to_observation(db_session, row)

    assert obs["identifier_type"] == "jur_slug"
    assert obs["identifier_value"] == "usa-wa"
    assert obs["jurisdiction_slug"] == "usa-wa"
    assert obs["jurisdiction_name"] == "Washington"
    assert obs["jurisdiction_type_slug"] == "state"


async def test_upsert_inserts_new_with_anchor(db_session, descriptor, state_type):
    pm_id = ULID()
    await descriptor.upsert_from_pm(db_session, _pm_record(pm_id=pm_id))

    row = (
        await db_session.execute(select(Jurisdiction).where(Jurisdiction.slug == "usa-wa"))
    ).scalar_one()
    assert row.name == "Washington"
    assert row.type_id == state_type.id
    assert row.pm_jurisdiction_id == pm_id
    assert row.valid_from == datetime(2022, 1, 1, tzinfo=UTC)
    assert row.recorded_at == datetime(2022, 1, 1, tzinfo=UTC)


async def test_upsert_updates_existing(db_session, descriptor, state_type):
    existing = Jurisdiction(
        slug="usa-wa", name="Old Name", type_id=state_type.id, recorded_at=datetime.now(UTC)
    )
    db_session.add(existing)
    await db_session.flush()

    await descriptor.upsert_from_pm(db_session, _pm_record(name="Washington"), existing=existing)

    assert existing.name == "Washington"
    rows = (await db_session.execute(select(Jurisdiction))).scalars().all()
    assert len(rows) == 1  # updated, not duplicated


async def test_local_match_by_slug(db_session, descriptor, state_type):
    row = Jurisdiction(
        slug="usa-wa-county-king",
        name="King County",
        type_id=state_type.id,
        recorded_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.flush()

    matched = await descriptor.local_match(db_session, {"slug": "usa-wa-county-king"})
    assert matched is not None
    assert matched.id == row.id
    assert await descriptor.local_match(db_session, {"slug": "nope"}) is None


async def test_last_updated_row_and_record(db_session, descriptor, state_type):
    row = Jurisdiction(
        slug="usa-wa", name="WA", type_id=state_type.id, recorded_at=datetime.now(UTC)
    )
    row.updated_at = datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated(row) == datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated({"updated_at": "2026-06-02T00:00:00Z"}) == datetime(
        2026, 6, 2, tzinfo=UTC
    )
