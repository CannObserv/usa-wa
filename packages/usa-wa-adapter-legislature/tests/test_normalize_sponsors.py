"""Tests for normalize/sponsors.py — Person + identifier + party + Senate seat (P1b 4/5)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from ulid import ULID as _ULID

from clearinghouse_core.adapter import FetchedPayload
from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_domain_legislative.identity import (
    Assignment,
    Person,
    PersonIdentifier,
    Role,
)
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.normalize.sponsors import normalize_sponsors

BIENNIUM = "2025-26"


def _member(id_, first, last, *, agency, party, district):
    return {
        "Id": id_,
        "Name": f"{first} {last}",
        "LongName": f"{'Senator' if agency == 'Senate' else 'Representative'} {last}",
        "Agency": agency,
        "Acronym": None,
        "Party": party,
        "District": district,
        "Phone": None,
        "Email": None,
        "FirstName": first,
        "LastName": last,
    }


def _blanked_stub(id_, agency):
    return {
        "Id": id_,
        "Name": " ",
        "LongName": f"{'Senator' if agency == 'Senate' else 'Representative'} ",
        "Agency": agency,
        "Party": None,
        "District": None,
        "FirstName": None,
        "LastName": None,
    }


def _payload(members):
    return FetchedPayload(
        url="https://wslwebservices.leg.wa.gov/SponsorService.asmx#GetSponsors",
        fetched_at=datetime.now(UTC),
        content_type="text/xml",
        body=b"",
        parsed=members,
    )


async def _add_ld(session, usa_wa, n: int):
    row = Jurisdiction(
        slug=f"usa-wa-ld-{n}",
        name=f"WA Legislative District {n}",
        type_id=usa_wa.type_id,
        pm_jurisdiction_id=_ULID(),
        recorded_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


@pytest.fixture
async def anchors(db_session, usa_wa):
    return await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )


async def _run(session, usa_wa, anchors, members):
    return await normalize_sponsors(
        _payload(members),
        session=session,
        anchors=anchors,
        biennium=BIENNIUM,
    )


# --- Person + identifier ------------------------------------------------------


async def test_person_and_identifier_emitted(db_session, usa_wa, anchors):
    members = [_member(101, "Ann", "Rivers", agency="Senate", party="R", district="18")]
    await _add_ld(db_session, usa_wa, 18)

    batch = await _run(db_session, usa_wa, anchors, members)

    persons = [e for e in batch.entities if isinstance(e, Person)]
    assert len(persons) == 1
    p = persons[0]
    assert p.source_id == "101"
    assert p.name_full == "Ann Rivers"
    assert p.name_first == "Ann" and p.name_last == "Rivers"
    assert p.name_used == "Senator Rivers"  # LongName differs from full name

    ids = [e for e in batch.entities if isinstance(e, PersonIdentifier)]
    assert len(ids) == 1
    assert ids[0].scheme == "wa_legislature_member_id"
    assert ids[0].value == "101"
    assert ids[0].person_id == p.id


async def test_non_person_stub_skipped(db_session, usa_wa, anchors):
    members = [
        _blanked_stub(2006, "Senate"),
        _member(102, "Joe", "Nguyen", agency="Senate", party="D", district="34"),
    ]
    await _add_ld(db_session, usa_wa, 34)

    batch = await _run(db_session, usa_wa, anchors, members)

    persons = [e for e in batch.entities if isinstance(e, Person)]
    assert {p.source_id for p in persons} == {"102"}  # the stub produced no Person


# --- party Assignments (R / D / independent) ----------------------------------


async def test_party_assignment_republican_and_democrat(db_session, usa_wa, anchors):
    await _add_ld(db_session, usa_wa, 18)
    await _add_ld(db_session, usa_wa, 34)
    members = [
        _member(101, "Ann", "Rivers", agency="Senate", party="R", district="18"),
        _member(102, "Joe", "Nguyen", agency="Senate", party="D", district="34"),
    ]

    batch = await _run(db_session, usa_wa, anchors, members)

    assignments = [e for e in batch.entities if isinstance(e, Assignment)]
    party_asgs = [a for a in assignments if a.source_id.split(":")[1] == "party"]
    assert len(party_asgs) == 2
    # each party Assignment points at the matching party Org's Member role
    roles = {r.id: r for r in batch.entities if isinstance(r, Role)}
    by_member = {a.source_id.split(":")[0]: roles[a.role_id] for a in party_asgs}
    assert by_member["101"].organization_id == anchors.party_ids["republican"]
    assert by_member["102"].organization_id == anchors.party_ids["democratic"]
    assert all(r.role_type == "member" and r.name == "Member" for r in by_member.values())


async def test_independent_gets_no_party_assignment(db_session, usa_wa, anchors):
    await _add_ld(db_session, usa_wa, 40)
    # Party None (independent) — a real person, but unaffiliated.
    members = [_member(103, "Ind", "Ependent", agency="Senate", party=None, district="40")]

    batch = await _run(db_session, usa_wa, anchors, members)

    assignments = [e for e in batch.entities if isinstance(e, Assignment)]
    assert not any(a.source_id.split(":")[1] == "party" for a in assignments)
    # still a Person + a seat Assignment
    assert any(isinstance(e, Person) for e in batch.entities)


async def test_party_full_word_encoding_canonicalizes(db_session, usa_wa, anchors):
    """Committee-endpoint spelling (Republican/Democrat) folds the same as R/D."""
    await _add_ld(db_session, usa_wa, 18)
    members = [_member(101, "Ann", "Rivers", agency="Senate", party="Republican", district="18")]

    batch = await _run(db_session, usa_wa, anchors, members)

    roles = {r.id: r for r in batch.entities if isinstance(r, Role)}
    party_asg = next(
        a for a in batch.entities if isinstance(a, Assignment) and ":party:" in a.source_id
    )
    assert roles[party_asg.role_id].organization_id == anchors.party_ids["republican"]


# --- Senate seat Assignments (step 5) -----------------------------------------


async def test_senate_member_gets_seat_role_and_assignment(db_session, usa_wa, anchors):
    ld = await _add_ld(db_session, usa_wa, 18)
    members = [_member(101, "Ann", "Rivers", agency="Senate", party="R", district="18")]

    batch = await _run(db_session, usa_wa, anchors, members)

    seat_roles = [
        r for r in batch.entities if isinstance(r, Role) and r.role_type == "state_senator"
    ]
    assert len(seat_roles) == 1
    seat = seat_roles[0]
    assert seat.organization_id == anchors.senate_id
    assert seat.jurisdiction_id == ld.id
    assert seat.qualifier is None
    assert seat.name == "State Senator"

    seat_asg = next(
        a for a in batch.entities if isinstance(a, Assignment) and ":chamber-senate:" in a.source_id
    )
    assert seat_asg.person_id == next(p.id for p in batch.entities if isinstance(p, Person))
    assert seat_asg.role_id == seat.id
    assert seat_asg.valid_from == date(2025, 1, 1)
    assert seat_asg.is_active is True


async def test_senate_seat_unresolved_ld_skips_seat_keeps_person(db_session, usa_wa, anchors):
    """No local LD jurisdiction → no seat, but Person + party still land."""
    # LD 18 intentionally NOT seeded.
    members = [_member(101, "Ann", "Rivers", agency="Senate", party="R", district="18")]

    batch = await _run(db_session, usa_wa, anchors, members)

    assert not any(isinstance(e, Role) and e.role_type == "state_senator" for e in batch.entities)
    assert not any(
        isinstance(a, Assignment) and ":chamber-senate:" in a.source_id for a in batch.entities
    )
    assert any(isinstance(e, Person) for e in batch.entities)
    assert any(isinstance(a, Assignment) and ":party:" in a.source_id for a in batch.entities)


async def test_seat_role_reused_across_two_calls(db_session, usa_wa, anchors):
    """Two Senate members over two runs, same LD across bienniums → one seat Role."""
    ld = await _add_ld(db_session, usa_wa, 18)
    m = [_member(101, "Ann", "Rivers", agency="Senate", party="R", district="18")]

    await _run(db_session, usa_wa, anchors, m)
    await _run(db_session, usa_wa, anchors, m)

    seats = (
        (
            await db_session.execute(
                select(Role).where(Role.role_type == "state_senator", Role.jurisdiction_id == ld.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(seats) == 1


# --- House deferral (step 5) --------------------------------------------------


async def test_house_member_no_chamber_role_or_assignment(db_session, usa_wa, anchors):
    members = [_member(201, "Peter", "Abbarno", agency="House", party="R", district="20")]

    batch = await _run(db_session, usa_wa, anchors, members)

    # Person + party, but no chamber Role/Assignment (deferred to #69).
    assert any(isinstance(e, Person) for e in batch.entities)
    assert any(isinstance(a, Assignment) and ":party:" in a.source_id for a in batch.entities)
    assert not any(
        isinstance(r, Role) and r.role_type in {"state_representative", "state_senator"}
        for r in batch.entities
    )
    assert not any(isinstance(a, Assignment) and ":chamber-" in a.source_id for a in batch.entities)


async def test_mid_biennium_mover_one_person_senate_seat_only(db_session, usa_wa, anchors):
    """Two named rows under one Id (House + Senate) → one Person; Senate row gives the seat,
    House row nothing."""
    await _add_ld(db_session, usa_wa, 34)
    members = [
        _member(34024, "Emily", "Alvarado", agency="House", party="D", district="34"),
        _member(34024, "Emily", "Alvarado", agency="Senate", party="D", district="34"),
    ]

    batch = await _run(db_session, usa_wa, anchors, members)

    persons = [e for e in batch.entities if isinstance(e, Person)]
    assert len(persons) == 1  # deduped by Id
    seats = [r for r in batch.entities if isinstance(r, Role) and r.role_type == "state_senator"]
    assert len(seats) == 1  # from the Senate row only
    party_asgs = [
        a for a in batch.entities if isinstance(a, Assignment) and ":party:" in a.source_id
    ]
    assert len(party_asgs) == 1  # one party Assignment (deduped)
