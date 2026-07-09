"""Tests for normalize/sponsors.py — Person + identifier only (#78-2c).

Party + Senate-seat tenure are no longer emitted here — they are archive-derived merged
spans (Phase B, tested in test_sponsor_span_emit / test_harvest_sponsor_spans). This
normalizer's sole job is the Person cluster the spans resolve against.
"""

from __future__ import annotations

from datetime import UTC, datetime

from clearinghouse_core.adapter import FetchedPayload
from clearinghouse_domain_legislative.identity import Assignment, Person, PersonIdentifier, Role
from usa_wa_adapter_legislature.normalize.sponsors import normalize_sponsors


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


async def _run(session, members):
    return await normalize_sponsors(_payload(members), session=session)


# --- Person + identifier ------------------------------------------------------


async def test_person_and_identifier_emitted(db_session, usa_wa):
    members = [_member(101, "Ann", "Rivers", agency="Senate", party="R", district="18")]

    batch = await _run(db_session, members)

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


async def test_non_person_stub_skipped(db_session, usa_wa):
    members = [
        _blanked_stub(2006, "Senate"),
        _member(102, "Joe", "Nguyen", agency="Senate", party="D", district="34"),
    ]

    batch = await _run(db_session, members)

    persons = [e for e in batch.entities if isinstance(e, Person)]
    assert {p.source_id for p in persons} == {"102"}  # the stub produced no Person


# --- Persons-only guarantee (#78-2c: no inline party/seat emission) -----------


async def test_no_assignments_or_roles_emitted(db_session, usa_wa):
    """Senate + House members, major party — yet the normalizer emits ZERO Assignment/Role.
    Party + Senate-seat tenure are archive-derived spans now (Phase B), not per-biennium."""
    members = [
        _member(101, "Ann", "Rivers", agency="Senate", party="R", district="18"),
        _member(201, "Peter", "Abbarno", agency="House", party="D", district="20"),
    ]

    batch = await _run(db_session, members)

    assert not any(isinstance(e, Assignment) for e in batch.entities)
    assert not any(isinstance(e, Role) for e in batch.entities)
    assert {p.source_id for p in batch.entities if isinstance(p, Person)} == {"101", "201"}


async def test_mid_biennium_mover_dedups_to_one_person(db_session, usa_wa):
    """Two named rows under one Id (House + Senate tenure) collapse to one Person."""
    members = [
        _member(34024, "Emily", "Alvarado", agency="House", party="D", district="34"),
        _member(34024, "Emily", "Alvarado", agency="Senate", party="D", district="34"),
    ]

    batch = await _run(db_session, members)

    persons = [e for e in batch.entities if isinstance(e, Person)]
    assert len(persons) == 1  # deduped by Id
    assert persons[0].source_id == "34024"
