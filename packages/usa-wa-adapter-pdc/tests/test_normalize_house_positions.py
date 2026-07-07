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
    build_senate_roster,
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
    # Both roster views derive from the one sponsors list (as the refresh does): all-House
    # sponsors → empty senate map → no #74 inference; add Senate rows to enable it.
    return await normalize_house_positions(
        _payload(winners),
        house_roster=build_house_roster(sponsors),
        anchors=anchors,
        session=session,
        biennium=BIENNIUM,
        senate_roster=build_senate_roster(sponsors),
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


def test_build_senate_roster_groups_by_ld_and_skips_blanks() -> None:
    out = build_senate_roster(
        [
            _sponsor("27504", "Vandana", "Slatter", party="D", district="48", agency="Senate"),
            _sponsor("100", "Amy", "Walen", party="D", district="48"),  # House, ignored
            {"Id": "9", "Agency": "Senate", "District": "5", "LastName": None},  # blank surname
            {"Id": "8", "Agency": "Senate", "District": "", "LastName": "Nobody"},  # blank district
        ]
    )
    assert set(out) == {48}
    assert [(e.member_id, e.folded_last) for e in out[48]] == [("27504", "slatter")]


# --- #74: mid-biennium replacement inference by within-LD elimination -----------------


async def test_infers_replacement_seat_for_house_to_senate_mover(db_session, usa_wa, anchors):
    """The verified LD 48 case: Slatter won Pos 1 in 2024 then moved to the Senate, so her
    PDC winner defers; Walen matched Pos 2; the leftover roster member Salahuddin is
    inferred into the vacated Pos 1 (no PDC id, reduced-confidence citation); Slatter's PDC
    identity is cross-linked onto her current (Senate) Person."""
    await _add_ld(db_session, usa_wa, 48)
    salahuddin = await _add_wsl_person(db_session, "35655", "Osman Salahuddin")
    walen = await _add_wsl_person(db_session, "29109", "Amy Walen")
    slatter = await _add_wsl_person(db_session, "27504", "Vandana Slatter")  # now a Senator
    sponsors = [
        _sponsor("35655", "Osman", "Salahuddin", party="D", district="48"),  # replacement
        _sponsor("29109", "Amy", "Walen", party="D", district="48"),
        _sponsor("27504", "Vandana", "Slatter", party="D", district="48", agency="Senate"),
    ]
    winners = [
        _winner("800", "Amy Walen", position="2", ld="48"),
        _winner("801", "Vandana Slatter", position="1", ld="48"),  # moved to Senate → deferred
    ]

    batch = await _run(db_session, anchors, winners, sponsors)

    assigns = {a.source_id: a for a in batch.entities if isinstance(a, Assignment)}
    assert set(assigns) == {"29109:chamber-house:2025-26", "35655:chamber-house:2025-26"}
    assert assigns["35655:chamber-house:2025-26"].person_id == salahuddin.id

    # Identifiers: Walen's (direct) + Slatter's mover cross-link onto her Senate Person.
    # The inferred replacement Salahuddin carries NO person_wa_pdc (no PDC winner row).
    idents = {i.person_id: i.value for i in batch.entities if isinstance(i, PersonIdentifier)}
    assert idents == {walen.id: "800", slatter.id: "801"}
    assert salahuddin.id not in idents

    # Inferred seat → reduced-confidence FactCitation on the assignment's role binding.
    inferred = assigns["35655:chamber-house:2025-26"]
    cites = [c for c in batch.citations if c.entity is inferred]
    assert len(cites) == 1 and cites[0].confidence < 1.0

    role = next(r for r in batch.entities if isinstance(r, Role) and r.id == inferred.role_id)
    assert role.qualifier == "Position 1"


async def test_both_reps_moved_same_biennium_no_inference(db_session, usa_wa, anchors):
    """Edge case: both LD reps moved to the Senate the same biennium → two deferrals + two
    unmatched members → ambiguous → no seat inference (conservative guard). But each mover's
    PDC identity is still cross-linked onto their current (Senate) Person."""
    await _add_ld(db_session, usa_wa, 5)
    await _add_wsl_person(db_session, "600", "Ann Alpha")
    await _add_wsl_person(db_session, "601", "Ben Beta")
    xavier = await _add_wsl_person(db_session, "700", "Old Xavier")  # now Senators
    yolanda = await _add_wsl_person(db_session, "701", "Old Yolanda")
    sponsors = [
        _sponsor("600", "Ann", "Alpha", party="D", district="5"),
        _sponsor("601", "Ben", "Beta", party="D", district="5"),
        _sponsor("700", "Old", "Xavier", party="D", district="5", agency="Senate"),
        _sponsor("701", "Old", "Yolanda", party="D", district="5", agency="Senate"),
    ]
    winners = [
        _winner("900", "Old Xavier", position="1", ld="5"),
        _winner("901", "Old Yolanda", position="2", ld="5"),
    ]

    batch = await _run(db_session, anchors, winners, sponsors)
    assert [e for e in batch.entities if isinstance(e, Assignment)] == []  # no seat inferred
    # Both movers cross-linked despite the ambiguous (un-inferrable) seats.
    idents = {i.person_id: i.value for i in batch.entities if isinstance(i, PersonIdentifier)}
    assert idents == {xavier.id: "900", yolanda.id: "901"}


async def test_unmatched_without_mover_signal_is_not_inferred(db_session, usa_wa, anchors):
    """Masking guard: a single unmatched winner + single unmatched member but NO
    same-LD Senator signal (the surname matches a senator in a *different* LD) → could be a
    name-match miss, so we don't infer."""
    await _add_ld(db_session, usa_wa, 7)
    await _add_wsl_person(db_session, "500", "Real Member")
    sponsors = [
        _sponsor("500", "Real", "Member", party="D", district="7"),
        _sponsor("800", "Ghost", "Winner", party="D", district="8", agency="Senate"),  # LD 8, not 7
    ]
    winners = [_winner("900", "Ghost Winner", position="1", ld="7")]

    batch = await _run(db_session, anchors, winners, sponsors)
    assert [e for e in batch.entities if isinstance(e, Assignment)] == []


async def test_inferred_seat_needs_replacement_person(db_session, usa_wa, anchors):
    """The elimination fires but the replacement's WSL Person isn't ingested yet → no rows."""
    await _add_ld(db_session, usa_wa, 48)
    await _add_wsl_person(db_session, "29109", "Amy Walen")  # Salahuddin NOT seeded
    sponsors = [
        _sponsor("35655", "Osman", "Salahuddin", party="D", district="48"),
        _sponsor("29109", "Amy", "Walen", party="D", district="48"),
        _sponsor("27504", "Vandana", "Slatter", party="D", district="48", agency="Senate"),
    ]
    winners = [
        _winner("800", "Amy Walen", position="2", ld="48"),
        _winner("801", "Vandana Slatter", position="1", ld="48"),
    ]

    batch = await _run(db_session, anchors, winners, sponsors)
    assigns = [a for a in batch.entities if isinstance(a, Assignment)]
    assert {a.source_id for a in assigns} == {"29109:chamber-house:2025-26"}


async def test_reconcile_attempted_person_absent_suppresses_unresolved_log(
    db_session, usa_wa, anchors, caplog
):
    """When the reconcile fires but the replacement Person is absent, only
    `pdc_house_person_absent` is logged — not a redundant `pdc_house_unresolved`."""
    await _add_ld(db_session, usa_wa, 48)
    await _add_wsl_person(db_session, "29109", "Amy Walen")  # replacement Salahuddin NOT seeded
    sponsors = [
        _sponsor("35655", "Osman", "Salahuddin", party="D", district="48"),
        _sponsor("29109", "Amy", "Walen", party="D", district="48"),
        _sponsor("27504", "Vandana", "Slatter", party="D", district="48", agency="Senate"),
    ]
    winners = [
        _winner("800", "Amy Walen", position="2", ld="48"),
        _winner("801", "Vandana Slatter", position="1", ld="48"),
    ]

    with caplog.at_level(logging.INFO):
        await _run(db_session, anchors, winners, sponsors)

    messages = [r.message for r in caplog.records]
    assert "pdc_house_person_absent" in messages
    assert "pdc_house_unresolved" not in messages


async def test_mover_person_absent_still_infers_replacement_seat(db_session, usa_wa, anchors):
    """The mover cross-link no-ops when the mover's Person isn't ingested, but the
    replacement's seat inference is independent and still fires."""
    await _add_ld(db_session, usa_wa, 48)
    salahuddin = await _add_wsl_person(db_session, "35655", "Osman Salahuddin")
    await _add_wsl_person(db_session, "29109", "Amy Walen")  # Slatter (mover) NOT seeded
    sponsors = [
        _sponsor("35655", "Osman", "Salahuddin", party="D", district="48"),
        _sponsor("29109", "Amy", "Walen", party="D", district="48"),
        _sponsor("27504", "Vandana", "Slatter", party="D", district="48", agency="Senate"),
    ]
    winners = [
        _winner("800", "Amy Walen", position="2", ld="48"),
        _winner("801", "Vandana Slatter", position="1", ld="48"),
    ]

    batch = await _run(db_session, anchors, winners, sponsors)
    assigns = {a.source_id for a in batch.entities if isinstance(a, Assignment)}
    assert "35655:chamber-house:2025-26" in assigns  # replacement still seated
    # No mover identifier (Slatter's Person absent); only Walen's direct one.
    idents = [i for i in batch.entities if isinstance(i, PersonIdentifier)]
    assert {i.value for i in idents} == {"800"} and salahuddin.id not in {
        i.person_id for i in idents
    }


async def test_unsynced_ld_logged_once_across_multiple_winners(db_session, usa_wa, anchors, caplog):
    """An unsynced LD is resolved + logged once, not once per winner (cached sentinel)."""
    # LD 99 never seeded as a Jurisdiction; two winners in it.
    sponsors = [_sponsor("100", "A", "One", party="D", district="99")]
    winners = [
        _winner("900", "A One", position="1", ld="99"),
        _winner("901", "B Two", position="2", ld="99"),
    ]

    with caplog.at_level(logging.WARNING):
        batch = await _run(db_session, anchors, winners, sponsors)

    assert batch.entities == []
    assert [r.message for r in caplog.records].count("pdc_house_unresolved_ld") == 1
