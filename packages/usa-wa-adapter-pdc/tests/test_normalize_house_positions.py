"""Tests for normalize/house_positions.py — PDC winner → person_wa_pdc + House seat.

The normalizer matches each PDC House winner to the *existing* WSL :class:`Person`
(within its LD, by folded last name + party), then emits a `person_wa_pdc` child
identifier and the `state_representative` seat Assignment (qualifier = Position N).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pytest
from ulid import ULID as _ULID
from usa_wa_adapter_pdc.normalize.house_positions import (
    build_house_roster,
    normalize_house_positions,
)

from clearinghouse_core.adapter import FetchedPayload
from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_domain_legislative.identity import Assignment, Person, PersonIdentifier, Role
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors

BIENNIUM = "2025-26"


def _sponsor(id_, first, last, *, party, district, agency="House"):
    return {
        "Id": id_,
        "Agency": agency,
        "Party": party,
        "District": district,
        "FirstName": first,
        "LastName": last,
    }


def _winner(person_id, filer_name, *, position, ld, party_code="D"):
    return {
        "person_id": person_id,
        "filer_name": filer_name,
        "position": position,
        "legislative_district": ld,
        "party_code": party_code,
        "office": "STATE REPRESENTATIVE",
        "general_election_status": "Won in general",
    }


def _payload(winners):
    return FetchedPayload(
        url="https://data.wa.gov/resource/3h9x-7bvm.json#house-winners:2024",
        fetched_at=datetime.now(UTC),
        content_type="application/json",
        body=b"[]",
        parsed=winners,
    )


async def _add_ld(session, usa_wa, n: int) -> Jurisdiction:
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


async def _add_wsl_person(session, member_id, name) -> Person:
    row = Person(source="usa_wa_legislature", source_id=str(member_id), name_full=name)
    session.add(row)
    await session.flush()
    return row


@pytest.fixture
async def anchors(db_session, usa_wa):
    return await bootstrap_synthetic_anchors(
        db_session, biennium=BIENNIUM, jurisdiction_id=usa_wa.id
    )


async def _run(session, anchors, winners, sponsors):
    return await normalize_house_positions(
        _payload(winners),
        house_roster=build_house_roster(sponsors),
        anchors=anchors,
        session=session,
        biennium=BIENNIUM,
    )


async def test_clean_two_rep_district_yields_two_seats(db_session, usa_wa, anchors):
    await _add_ld(db_session, usa_wa, 42)
    await _add_wsl_person(db_session, "100", "Alicia Rule")
    await _add_wsl_person(db_session, "200", "Joe Timmons")
    sponsors = [
        _sponsor("100", "Alicia", "Rule", party="D", district="42"),
        _sponsor("200", "Joe", "Timmons", party="D", district="42"),
    ]
    winners = [
        _winner("900", "Alicia Rule", position="1", ld="42"),
        _winner("901", "Joe Timmons", position="2", ld="42"),
    ]

    batch = await _run(db_session, anchors, winners, sponsors)

    roles = [e for e in batch.entities if isinstance(e, Role)]
    idents = [e for e in batch.entities if isinstance(e, PersonIdentifier)]
    assigns = [e for e in batch.entities if isinstance(e, Assignment)]
    assert {r.qualifier for r in roles} == {"Position 1", "Position 2"}
    assert all(
        r.role_type == "state_representative" and r.organization_id == anchors.house_id
        for r in roles
    )
    assert {i.scheme for i in idents} == {"wa_pdc"}
    assert {i.value for i in idents} == {"900", "901"}
    assert len(assigns) == 2
    # The seat Assignment is keyed on the WSL member id (role is a value).
    assert {a.source_id for a in assigns} == {
        "100:chamber-house:2025-26",
        "200:chamber-house:2025-26",
    }
    assert all(a.valid_from == date(2025, 1, 1) and a.is_active for a in assigns)


async def test_identifier_and_assignment_bind_to_existing_wsl_person(db_session, usa_wa, anchors):
    await _add_ld(db_session, usa_wa, 42)
    person = await _add_wsl_person(db_session, "100", "Alicia Rule")
    batch = await _run(
        db_session,
        anchors,
        [_winner("900", "Alicia Rule", position="1", ld="42")],
        [_sponsor("100", "Alicia", "Rule", party="D", district="42")],
    )
    ident = next(e for e in batch.entities if isinstance(e, PersonIdentifier))
    assign = next(e for e in batch.entities if isinstance(e, Assignment))
    assert ident.person_id == person.id
    assert assign.person_id == person.id


async def test_messy_filer_name_matches_within_ld(db_session, usa_wa, anchors):
    await _add_ld(db_session, usa_wa, 25)
    await _add_wsl_person(db_session, "300", "Cyndy Jacobsen")
    batch = await _run(
        db_session,
        anchors,
        [
            _winner(
                "950", "JACOBSEN CYNTHIA P (Cyndy Jacobsen)", position="2", ld="25", party_code="R"
            )
        ],
        [_sponsor("300", "Cynthia", "Jacobsen", party="R", district="25")],
    )
    assert any(isinstance(e, Assignment) for e in batch.entities)


async def test_party_disambiguates_shared_surname(db_session, usa_wa, anchors):
    await _add_ld(db_session, usa_wa, 10)
    await _add_wsl_person(db_session, "400", "Dave Smith")
    await _add_wsl_person(db_session, "401", "Sara Smith")
    sponsors = [
        _sponsor("400", "Dave", "Smith", party="R", district="10"),
        _sponsor("401", "Sara", "Smith", party="D", district="10"),
    ]
    batch = await _run(
        db_session,
        anchors,
        [_winner("960", "Sara Smith", position="1", ld="10", party_code="D")],
        sponsors,
    )
    assign = next(e for e in batch.entities if isinstance(e, Assignment))
    assert assign.source_id == "401:chamber-house:2025-26"  # the D Smith, not the R


async def test_member_matched_by_two_winners_is_not_double_seated(db_session, usa_wa, anchors):
    """A pathological token overlap (both winners resolve to the one WSL member) yields a
    single Assignment for that member — the second is warned + skipped, not silently
    deduped into a stray positioned Role."""
    await _add_ld(db_session, usa_wa, 15)
    await _add_wsl_person(db_session, "500", "Al Smith")  # only one member in the LD
    sponsors = [_sponsor("500", "Al", "Smith", party="D", district="15")]
    winners = [
        _winner("970", "Al Smith", position="1", ld="15"),
        _winner("971", "Bo Smith", position="2", ld="15"),  # also token-matches "Smith"
    ]

    batch = await _run(db_session, anchors, winners, sponsors)

    assigns = [e for e in batch.entities if isinstance(e, Assignment)]
    assert len(assigns) == 1
    assert assigns[0].source_id == "500:chamber-house:2025-26"
    # Only the first winner's Position 1 seat Role was minted (no stray Position 2).
    roles = [e for e in batch.entities if isinstance(e, Role)]
    assert {r.qualifier for r in roles} == {"Position 1"}


def test_build_house_roster_filters_non_house_and_blank() -> None:
    roster = build_house_roster(
        [
            _sponsor("100", "Alicia", "Rule", party="D", district="42"),  # valid House
            _sponsor("200", "Sam", "Solon", party="R", district="42", agency="Senate"),  # not House
            _sponsor("300", "No", "District", party="D", district=""),  # blank district
            {  # name-blanked stub (no LastName)
                "Id": "400",
                "Agency": "House",
                "Party": "D",
                "District": "10",
                "FirstName": None,
                "LastName": None,
            },
        ]
    )
    assert set(roster) == {42}
    assert [e.member_id for e in roster[42]] == ["100"]


async def test_unresolved_ld_emits_nothing(db_session, usa_wa, anchors):
    # LD 99 is never seeded as a Jurisdiction → resolve_ld_jurisdiction returns None → skip.
    await _add_wsl_person(db_session, "700", "Jo Nobody")
    batch = await _run(
        db_session,
        anchors,
        [_winner("990", "Jo Nobody", position="1", ld="99")],
        [_sponsor("700", "Jo", "Nobody", party="D", district="99")],
    )
    assert batch.entities == []


async def test_double_match_on_absent_person_is_not_reported_as_double_match(
    db_session, usa_wa, anchors, caplog
):
    """When the shared member has no WSL Person yet, both winners report `person_absent`
    — the first must not mark the member seen and mislabel the second as a double-match."""
    await _add_ld(db_session, usa_wa, 16)  # member 600 deliberately NOT seeded as a Person
    sponsors = [_sponsor("600", "Al", "Smith", party="D", district="16")]
    winners = [
        _winner("980", "Al Smith", position="1", ld="16"),
        _winner("981", "Bo Smith", position="2", ld="16"),
    ]

    with caplog.at_level(logging.WARNING):
        batch = await _run(db_session, anchors, winners, sponsors)

    assert batch.entities == []
    messages = [r.message for r in caplog.records]
    assert "pdc_house_member_double_matched" not in messages
    assert messages.count("pdc_house_person_absent") == 2


async def test_unresolved_winner_emits_nothing(db_session, usa_wa, anchors):
    await _add_ld(db_session, usa_wa, 42)
    await _add_wsl_person(db_session, "100", "Alicia Rule")
    batch = await _run(
        db_session,
        anchors,
        [_winner("900", "Ghost Candidate", position="1", ld="42")],  # matches no WSL member
        [_sponsor("100", "Alicia", "Rule", party="D", district="42")],
    )
    assert batch.entities == []


async def test_invalid_position_skipped(db_session, usa_wa, anchors):
    await _add_ld(db_session, usa_wa, 42)
    await _add_wsl_person(db_session, "100", "Alicia Rule")
    batch = await _run(
        db_session,
        anchors,
        [_winner("900", "Alicia Rule", position="0", ld="42")],  # not a House position
        [_sponsor("100", "Alicia", "Rule", party="D", district="42")],
    )
    assert batch.entities == []


async def test_person_not_yet_ingested_skipped(db_session, usa_wa, anchors):
    await _add_ld(db_session, usa_wa, 42)  # no WSL Person seeded
    batch = await _run(
        db_session,
        anchors,
        [_winner("900", "Alicia Rule", position="1", ld="42")],
        [_sponsor("100", "Alicia", "Rule", party="D", district="42")],
    )
    assert batch.entities == []


async def test_seat_role_reused_when_present(db_session, usa_wa, anchors):
    """A pre-existing seat Role (same structural source_id) is reused, not duplicated."""
    ld = await _add_ld(db_session, usa_wa, 42)
    await _add_wsl_person(db_session, "100", "Alicia Rule")
    existing = Role(
        source="usa_wa_legislature",
        source_id="seat:house:ld-42:position-1",
        organization_id=anchors.house_id,
        name="State Representative",
        role_type="state_representative",
        jurisdiction_id=ld.id,
        qualifier="Position 1",
    )
    db_session.add(existing)
    await db_session.flush()

    batch = await _run(
        db_session,
        anchors,
        [_winner("900", "Alicia Rule", position="1", ld="42")],
        [_sponsor("100", "Alicia", "Rule", party="D", district="42")],
    )
    role = next(e for e in batch.entities if isinstance(e, Role))
    assert role.id == existing.id
