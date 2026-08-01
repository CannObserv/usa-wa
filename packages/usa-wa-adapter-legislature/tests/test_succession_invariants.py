"""Succession invariant checks (#107) — chamber-count + duplicate-occupancy."""

from datetime import date

from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from usa_wa_adapter_legislature.succession_invariants import (
    MemberConflict,
    SeatConflict,
    _run_audit,
    audit_exit_code,
    check_invariants,
    duplicate_occupancy_detail,
    member_duplicate_detail,
    sweep_years,
)


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


async def _person(session, mid, name="M"):
    p = Person(source="usa_wa_legislature", source_id=mid, name_full=name)
    session.add(p)
    await session.flush()
    return p


async def _span(session, person, role, *, frm, to, active):
    """An Assignment with an explicit validity window (the #119 point-in-time audit)."""
    row = Assignment(
        source="usa_wa_legislature",
        source_id=f"{person.source_id}:{role.source_id}",
        person_id=person.id,
        role_id=role.id,
        valid_from=frm,
        valid_to=to,
        is_active=active,
    )
    session.add(row)
    await session.flush()
    return row


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


# --- #119: point-in-time (as-of) historical audit -----------------------------------------


async def test_as_of_counts_a_closed_span_that_covers_the_date(db_session, usa_wa):
    """A closed (is_active=False) span still occupies the seat at a date inside its window —
    the open-cohort gate misses it, the as-of audit catches it."""
    senate = await _org(db_session, usa_wa, "Senate")
    s1 = await _seat(db_session, senate, "seat:senate:ld-5", "state_senator")
    await _span(
        db_session,
        await _person(db_session, "1"),
        s1,
        frm=date(2009, 1, 1),
        to=date(2010, 12, 31),
        active=False,
    )
    # Open cohort sees nothing (span is closed); as-of 2009-01-01 counts it.
    open_result = await check_invariants(db_session, expected_senate=1, expected_house=0)
    assert open_result.senate_open == 0
    asof = await check_invariants(
        db_session, expected_senate=1, expected_house=0, as_of=date(2009, 1, 1)
    )
    assert asof.senate_open == 1


async def test_as_of_excludes_a_span_that_ended_before_the_date(db_session, usa_wa):
    """A span whose valid_to precedes the probe date does not occupy the seat at that date."""
    senate = await _org(db_session, usa_wa, "Senate")
    s1 = await _seat(db_session, senate, "seat:senate:ld-5", "state_senator")
    await _span(
        db_session,
        await _person(db_session, "1"),
        s1,
        frm=date(2007, 1, 1),
        to=date(2008, 12, 31),
        active=False,
    )
    asof = await check_invariants(
        db_session, expected_senate=1, expected_house=0, as_of=date(2009, 1, 1)
    )
    assert asof.senate_open == 0


async def test_as_of_surfaces_historical_duplicate_occupancy(db_session, usa_wa):
    """Two members whose floor-dated spans both cover the biennium start = the #119 overlap the
    daily open-cohort gate can't see once the predecessor's span has closed."""
    house = await _org(db_session, usa_wa, "House")
    seat = await _seat(db_session, house, "seat:house:ld-16:position-2", "state_representative")
    grant = await _person(db_session, "1", "Laura Grant")
    nealey = await _person(db_session, "2", "Terry Nealey")
    # Predecessor: floor start, closed at end of the biennium.
    await _span(db_session, grant, seat, frm=date(2009, 1, 1), to=date(2010, 12, 31), active=False)
    # Successor: floor start too (the wire can't date the mid-biennium handoff), still open.
    await _span(db_session, nealey, seat, frm=date(2009, 1, 1), to=None, active=True)

    conflicts = await duplicate_occupancy_detail(db_session, as_of=date(2009, 1, 1))
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.seat == "seat:house:ld-16:position-2"
    assert conflict.role_type == "state_representative"
    assert conflict.occupants == ["Laura Grant", "Terry Nealey"]


async def test_duplicate_occupancy_detail_clean_when_sequential(db_session, usa_wa):
    """No conflict when the predecessor closed before the successor's window opens."""
    house = await _org(db_session, usa_wa, "House")
    seat = await _seat(db_session, house, "seat:house:ld-16:position-2", "state_representative")
    await _span(
        db_session,
        await _person(db_session, "1", "A"),
        seat,
        frm=date(2007, 1, 1),
        to=date(2008, 12, 31),
        active=False,
    )
    await _span(
        db_session,
        await _person(db_session, "2", "B"),
        seat,
        frm=date(2009, 1, 1),
        to=None,
        active=True,
    )
    # At 2009-01-01 only the successor covers the seat — no duplicate.
    assert await duplicate_occupancy_detail(db_session, as_of=date(2009, 1, 1)) == []


def test_sweep_years_rolls_even_floor_back_and_includes_endpoint():
    assert sweep_years(1991, 1995) == [1991, 1993, 1995]
    assert sweep_years(1990, 1994) == [1989, 1991, 1993]  # even floor → prior odd
    assert sweep_years(2025, 2025) == [2025]


async def test_run_audit_reports_and_returns_conflicts(db_session, usa_wa):
    """The sweep collects every probe's conflicts flat (for the --strict gate)."""
    house = await _org(db_session, usa_wa, "House")
    seat = await _seat(db_session, house, "seat:house:ld-16:position-2", "state_representative")
    await _span(
        db_session,
        await _person(db_session, "1", "Laura Grant"),
        seat,
        frm=date(2009, 1, 1),
        to=date(2010, 12, 31),
        active=False,
    )
    await _span(
        db_session,
        await _person(db_session, "2", "Terry Nealey"),
        seat,
        frm=date(2009, 1, 1),
        to=None,
        active=True,
    )
    outcome = await _run_audit(db_session, probes=[date(2007, 1, 1), date(2009, 1, 1)])
    # 2007 clean, 2009 overlap → exactly one seat conflict surfaced across the two probes.
    assert outcome.seat_conflicts == [
        SeatConflict(
            seat="seat:house:ld-16:position-2",
            role_type="state_representative",
            occupants=["Laura Grant", "Terry Nealey"],
        )
    ]
    assert outcome.member_conflicts == []
    assert outcome.total == 1


async def test_member_duplicate_detail_surfaces_two_seats_one_chamber(db_session, usa_wa):
    """A member holding two distinct same-chamber seats at the probe date — the member-side of
    the #107 duplicate check, point-in-time (#119)."""
    senate = await _org(db_session, usa_wa, "Senate")
    s5 = await _seat(db_session, senate, "seat:senate:ld-5", "state_senator")
    s6 = await _seat(db_session, senate, "seat:senate:ld-6", "state_senator")
    dupe = await _person(db_session, "1", "Double Booked")
    await _span(db_session, dupe, s5, frm=date(2009, 1, 1), to=None, active=True)
    await _span(db_session, dupe, s6, frm=date(2009, 1, 1), to=None, active=True)

    conflicts = await member_duplicate_detail(db_session, as_of=date(2009, 1, 1))
    assert conflicts == [
        MemberConflict(
            member="Double Booked",
            role_type="state_senator",
            seats=["seat:senate:ld-5", "seat:senate:ld-6"],
        )
    ]


async def test_member_duplicate_detail_ignores_one_seat_held_twice(db_session, usa_wa):
    """One seat via two rows is a seat-duplicate, not a member-duplicate — deduped on distinct
    seat source_id so it isn't double-reported here."""
    senate = await _org(db_session, usa_wa, "Senate")
    s5 = await _seat(db_session, senate, "seat:senate:ld-5", "state_senator")
    dupe = await _person(db_session, "1", "Grant / Nealey Seat")
    db_session.add(
        Assignment(
            source="usa_wa_legislature",
            source_id="a",
            person_id=dupe.id,
            role_id=s5.id,
            valid_from=date(2009, 1, 1),
            valid_to=None,
            is_active=True,
        )
    )
    db_session.add(
        Assignment(
            source="usa_wa_legislature",
            source_id="b",
            person_id=dupe.id,
            role_id=s5.id,
            valid_from=date(2009, 6, 1),
            valid_to=None,
            is_active=True,
        )
    )
    await db_session.flush()
    assert await member_duplicate_detail(db_session, as_of=date(2009, 1, 1)) == []


async def test_member_duplicate_detail_keys_on_identity_not_name(db_session, usa_wa):
    """Two DISTINCT persons sharing a name (this dataset has Bob McCaslin Sr./Jr.), each holding
    one seat in the same chamber, must NOT merge into a phantom two-seat conflict — identity is
    person_id, name is display only (#119 CR3)."""
    senate = await _org(db_session, usa_wa, "Senate")
    s5 = await _seat(db_session, senate, "seat:senate:ld-5", "state_senator")
    s6 = await _seat(db_session, senate, "seat:senate:ld-6", "state_senator")
    sr = await _person(db_session, "1", "Bob McCaslin")
    jr = await _person(db_session, "2", "Bob McCaslin")  # same name, distinct person
    await _span(db_session, sr, s5, frm=date(2009, 1, 1), to=None, active=True)
    await _span(db_session, jr, s6, frm=date(2009, 1, 1), to=None, active=True)

    assert await member_duplicate_detail(db_session, as_of=date(2009, 1, 1)) == []


def test_audit_exit_code():
    """The #119 audit exit contract: 1 only when --strict AND a duplicate was found."""
    assert audit_exit_code(strict=True, conflict_count=3) == 1
    assert audit_exit_code(strict=True, conflict_count=0) == 0  # strict but clean
    assert audit_exit_code(strict=False, conflict_count=3) == 0  # report-only default
    assert audit_exit_code(strict=False, conflict_count=0) == 0
