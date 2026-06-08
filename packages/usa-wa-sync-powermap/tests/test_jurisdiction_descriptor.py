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


async def test_upsert_auto_creates_unknown_type(db_session, descriptor):
    """An unknown type slug is minted locally from the embedded PM type object
    (no poison-pill IntegrityError, no PM round-trip needed)."""
    record = _pm_record(slug="usa-wa-sd-seattle", name="Seattle School District")
    record["type"] = {
        "id": str(ULID()),
        "slug": "school_district",
        "display_name": "School District",
    }

    row = await descriptor.upsert_from_pm(db_session, record)

    assert row is not None
    jt = (
        await db_session.execute(
            select(JurisdictionType).where(JurisdictionType.slug == "school_district")
        )
    ).scalar_one()
    assert jt.display_name == "School District"
    assert row.type_id == jt.id


async def test_upsert_skips_record_without_type(db_session, descriptor):
    """A record carrying no resolvable type is skipped (logged), not inserted as
    an invalid NULL-type row that would wedge the cycle."""
    record = _pm_record(slug="usa-wa-mystery")
    record["type"] = {}

    result = await descriptor.upsert_from_pm(db_session, record)

    assert result is None
    rows = (
        (
            await db_session.execute(
                select(Jurisdiction).where(Jurisdiction.slug == "usa-wa-mystery")
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_upsert_preserves_recorded_at_on_update_without_field(
    db_session, descriptor, state_type
):
    """recorded_at mirrors PM's clock — an update record that omits it must not
    re-stamp now() and churn the field."""
    original = datetime(2020, 5, 5, tzinfo=UTC)
    existing = Jurisdiction(slug="usa-wa", name="Old", type_id=state_type.id, recorded_at=original)
    db_session.add(existing)
    await db_session.flush()

    record = _pm_record(name="Washington")
    record.pop("recorded_at")
    await descriptor.upsert_from_pm(db_session, record, existing=existing)

    assert existing.recorded_at == original


async def test_to_observation_nulls_unresolved_type_without_raising(db_session, descriptor):
    """An orphaned type_id yields a null type slug (PM rejects → outbox), not a
    crash that would poison the drain cycle."""
    row = Jurisdiction(slug="usa-wa", name="WA", type_id=ULID(), recorded_at=datetime.now(UTC))

    obs = await descriptor.to_observation(db_session, row)

    assert obs["jurisdiction_type_slug"] is None
