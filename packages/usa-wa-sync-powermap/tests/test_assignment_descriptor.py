"""AssignmentDescriptor tests — (person, role) observation + dual dependency gating."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.identity import (
    Assignment,
    Organization,
    Person,
    Role,
)
from usa_wa_sync_powermap.descriptors import AssignmentDescriptor


@pytest.fixture
def descriptor() -> AssignmentDescriptor:
    return AssignmentDescriptor()


async def _scaffold(session, *, person_anchor=None, role_anchor=None, person_id_set=True):
    org = Organization(
        source="usa_wa_legislature",
        source_id="HOUSE",
        name="House",
        org_type="chamber",
        pm_organization_id=ULID(),
    )
    session.add(org)
    await session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id="R-1",
        organization_id=org.id,
        name="Member",
        role_type="elected_member",
        pm_role_id=role_anchor,
    )
    person = Person(
        source="usa_wa_legislature",
        source_id="M-1",
        name_full="Jane Doe",
        pm_person_id=person_anchor,
    )
    session.add_all([role, person])
    await session.flush()
    assignment = Assignment(
        source="usa_wa_legislature",
        source_id="A-1",
        person_id=person.id if person_id_set else None,
        holder_name_raw=None if person_id_set else "Jane Doe",
        role_id=role.id,
        valid_from=date(2025, 1, 1),
        valid_to=None,
        is_active=True,
    )
    session.add(assignment)
    await session.flush()
    return assignment, person, role


async def test_dependencies_ready_requires_both_anchors(db_session, descriptor):
    # neither anchored
    a, person, role = await _scaffold(db_session)
    assert await descriptor.dependencies_ready(db_session, a) is False

    # only person anchored
    person.pm_person_id = ULID()
    await db_session.flush()
    assert await descriptor.dependencies_ready(db_session, a) is False

    # both anchored
    role.pm_role_id = ULID()
    await db_session.flush()
    assert await descriptor.dependencies_ready(db_session, a) is True


async def test_dependencies_not_ready_without_person_id(db_session, descriptor):
    """An assignment with only a raw holder name can never be sent to PM."""
    a, _person, role = await _scaffold(db_session, role_anchor=ULID(), person_id_set=False)
    assert await descriptor.dependencies_ready(db_session, a) is False


async def test_to_observation_keys_on_person_and_role_pm_ids(db_session, descriptor):
    person_pm, role_pm = ULID(), ULID()
    a, _p, _r = await _scaffold(db_session, person_anchor=person_pm, role_anchor=role_pm)

    obs = await descriptor.to_observation(db_session, a)

    assert obs == {
        "person_id": str(person_pm),
        "role_id": str(role_pm),
        "start_date": "2025-01-01",
        "end_date": None,
        "is_current": True,
    }


async def test_local_match_by_anchor(db_session, descriptor):
    pm_id = ULID()
    a, _p, _r = await _scaffold(db_session, person_anchor=ULID(), role_anchor=ULID())
    a.pm_assignment_id = pm_id
    await db_session.flush()

    assert (await descriptor.local_match(db_session, {"id": str(pm_id)})).id == a.id
    assert await descriptor.local_match(db_session, {"id": str(ULID())}) is None


async def test_upsert_adopts_status_dates_and_anchor(db_session, descriptor):
    a, _p, _r = await _scaffold(db_session, person_anchor=ULID(), role_anchor=ULID())
    pm_id = ULID()
    record = {
        "id": str(pm_id),
        "is_current": False,
        "start_date": "2024-01-10",
        "end_date": "2025-12-31",
        "updated_at": "2030-01-01T00:00:00Z",
    }

    result = await descriptor.upsert_from_pm(db_session, record, existing=a)

    assert result is a
    assert a.is_active is False
    assert a.valid_from == date(2024, 1, 10)
    assert a.valid_to == date(2025, 12, 31)
    assert a.pm_assignment_id == pm_id


async def test_upsert_update_only_skips_unknown(db_session, descriptor):
    result = await descriptor.upsert_from_pm(db_session, {"id": str(ULID()), "is_current": True})
    assert result is None
    assert (await db_session.execute(select(Assignment))).scalars().all() == []


async def test_upsert_mirrors_pm_archived_at_to_retired_tombstone(db_session, descriptor):
    """PM archival on an anchored assignment mirrors onto ``archived_at`` (usa-wa#41)."""
    pm_id = ULID()
    a, _p, _r = await _scaffold(db_session, person_anchor=ULID(), role_anchor=ULID())
    a.pm_assignment_id = pm_id
    await db_session.flush()
    assert a.archived_at is None

    record = {"id": str(pm_id), "is_current": True, "archived_at": "2026-06-20T00:00:00Z"}
    result = await descriptor.upsert_from_pm(db_session, record, existing=a)

    assert result is a
    assert a.archived_at == datetime(2026, 6, 20, tzinfo=UTC)


async def test_upsert_clears_tombstone_when_pm_unarchives(db_session, descriptor):
    """PM un-archiving an assignment clears the mirrored tombstone."""
    pm_id = ULID()
    a, _p, _r = await _scaffold(db_session, person_anchor=ULID(), role_anchor=ULID())
    a.pm_assignment_id = pm_id
    a.archived_at = datetime(2026, 6, 20, tzinfo=UTC)
    await db_session.flush()

    result = await descriptor.upsert_from_pm(
        db_session, {"id": str(pm_id), "is_current": True}, existing=a
    )

    assert result is a
    assert a.archived_at is None


async def test_last_updated_row_and_record(db_session, descriptor):
    a, _p, _r = await _scaffold(db_session)
    a.updated_at = datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated(a) == datetime(2026, 6, 1, tzinfo=UTC)
    assert descriptor.last_updated({"updated_at": "2026-06-02T00:00:00Z"}) == datetime(
        2026, 6, 2, tzinfo=UTC
    )
