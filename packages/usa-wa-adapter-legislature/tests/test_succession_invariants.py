"""Succession invariant checks (#107) — chamber-count + duplicate-occupancy."""

from datetime import date

from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from usa_wa_adapter_legislature.succession_invariants import check_invariants


async def _org(session, usa_wa, name):
    org = Organization(
        source="usa_wa_legislature",
        source_id=f"org-{name}",
        jurisdiction_id=usa_wa.id,
        name=name,
        org_type="chamber",
    )
    session.add(org)
    await session.flush()
    return org


async def _seat(session, org, sid, role_type):
    role = Role(
        source="usa_wa_legislature",
        source_id=sid,
        organization_id=org.id,
        name=sid,  # distinct per seat (title-keyed uq_roles_org_name is (org, name))
        role_type=role_type,
    )
    session.add(role)
    await session.flush()
    return role


async def _person(session, mid):
    p = Person(source="usa_wa_legislature", source_id=mid, name_full="M")
    session.add(p)
    await session.flush()
    return p


async def _occupy(session, person, role, *, active=True, deleted=False):
    row = Assignment(
        source="usa_wa_legislature",
        source_id=f"{person.source_id}:{role.source_id}",
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2025, 1, 1),
        valid_to=None,
        is_active=active,
    )
    if deleted:
        row.deleted_at = date(2025, 6, 1)
        row.is_active = False
    session.add(row)
    await session.flush()
    return row


async def test_balanced_cohort_is_ok(db_session, usa_wa):
    senate = await _org(db_session, usa_wa, "Senate")
    house = await _org(db_session, usa_wa, "House")
    s1 = await _seat(db_session, senate, "seat:sen:5", "state_senator")
    h1 = await _seat(db_session, house, "seat:hou:5:1", "state_representative")
    h2 = await _seat(db_session, house, "seat:hou:5:2", "state_representative")
    await _occupy(db_session, await _person(db_session, "1"), s1)
    await _occupy(db_session, await _person(db_session, "2"), h1)
    await _occupy(db_session, await _person(db_session, "3"), h2)

    result = await check_invariants(db_session, expected_senate=1, expected_house=2)
    assert result.ok
    assert result.senate_open == 1 and result.house_open == 2


async def test_ghost_open_predecessor_trips_the_count(db_session, usa_wa):
    """A second open senator (ghost-open predecessor) → 2 vs expected 1 → violation."""
    senate = await _org(db_session, usa_wa, "Senate")
    s1 = await _seat(db_session, senate, "seat:sen:5", "state_senator")
    s2 = await _seat(db_session, senate, "seat:sen:6", "state_senator")
    await _occupy(db_session, await _person(db_session, "1"), s1)
    await _occupy(db_session, await _person(db_session, "2"), s2)  # the ghost

    result = await check_invariants(db_session, expected_senate=1, expected_house=0)
    assert not result.ok
    assert result.senate_open == 2


async def test_deleted_assignment_not_counted(db_session, usa_wa):
    """A tombstoned (deleted_at) assignment is excluded from the open cohort."""
    senate = await _org(db_session, usa_wa, "Senate")
    s1 = await _seat(db_session, senate, "seat:sen:5", "state_senator")
    await _occupy(db_session, await _person(db_session, "1"), s1)
    await _occupy(db_session, await _person(db_session, "2"), s1, deleted=True)

    result = await check_invariants(db_session, expected_senate=1, expected_house=0)
    assert result.ok
    assert result.senate_open == 1


async def test_two_occupants_one_seat_is_a_duplicate(db_session, usa_wa):
    senate = await _org(db_session, usa_wa, "Senate")
    s1 = await _seat(db_session, senate, "seat:sen:5", "state_senator")
    # Both open on the SAME seat Role — the two-open-senators-in-one-LD shape.
    p1 = await _person(db_session, "1")
    p2 = await _person(db_session, "2")
    db_session.add(
        Assignment(
            source="usa_wa_legislature",
            source_id="a",
            person_id=p1.id,
            role_id=s1.id,
            valid_from=date(2025, 1, 1),
            valid_to=None,
            is_active=True,
        )
    )
    db_session.add(
        Assignment(
            source="usa_wa_legislature",
            source_id="b",
            person_id=p2.id,
            role_id=s1.id,
            valid_from=date(2025, 6, 3),
            valid_to=None,
            is_active=True,
        )
    )
    await db_session.flush()

    result = await check_invariants(db_session, expected_senate=2, expected_house=0)
    assert result.duplicate_seats and result.duplicate_seats[0][1] == 2
    assert not result.ok
