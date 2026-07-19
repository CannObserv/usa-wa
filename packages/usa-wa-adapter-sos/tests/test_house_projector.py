"""Unit tests for the pure House-seat projector (#101).

The re-partition's core projection: the sitting House roster (WSL — who sits / LD / party)
joined to the SOS filing archive (the Position 1/2 qualifier) → tenure
:class:`~usa_wa_adapter_legislature.tenure_spans.Observation`s keyed on the House span
discriminator. Pure — no DB, no session. A member with no resolvable SOS position emits nothing
(OQ1: post-1965 "position unknown" is a data gap, not a position-less seat).
"""

from __future__ import annotations

from usa_wa_adapter_pdc.normalize.pdc_matching import build_house_roster
from usa_wa_adapter_pdc.normalize.pdc_observations import KIND_HOUSE
from usa_wa_adapter_sos.filings.normalize import build_house_filings
from usa_wa_adapter_sos.house.projector import build_house_seat_observations

from usa_wa_adapter_legislature.tenure_spans import Observation

BIENNIUM = "2013-14"


def _sponsor(mid, ld, last, *, party="Democrat", first="Ann"):
    return {
        "Id": mid,
        "FirstName": first,
        "LastName": last,
        "District": str(ld),
        "Party": party,
        "Agency": "House",
        "Name": f"{first} {last}",
    }


def _filing(ld, position, ballot_name, *, party="(Prefers Democratic Party)"):
    return {
        "RaceName": f"State Representative Pos. {position}",
        "RaceJurisdictionName": f"Legislative District {ld}",
        "BallotName": ballot_name,
        "PartyName": party,
    }


def test_member_with_sos_filing_yields_positioned_observation():
    roster = build_house_roster([_sponsor(100, 5, "Rivers")])
    filings = build_house_filings([_filing(5, 1, "Ann Rivers")])

    proj = build_house_seat_observations(roster, filings, biennium=BIENNIUM)

    assert proj.observations == [Observation("100", KIND_HOUSE, "ld-5-position-1", BIENNIUM)]
    assert proj.summary["matched"] == 1
    assert proj.summary["missing_position"] == 0


def test_member_without_sos_position_emits_nothing():
    # Position unknown (no filing in the LD) → no seat, counted missing_position (OQ1).
    roster = build_house_roster([_sponsor(200, 9, "Jones")])
    filings = build_house_filings([])  # empty SOS cohort

    proj = build_house_seat_observations(roster, filings, biennium=BIENNIUM)

    assert proj.observations == []
    assert proj.summary["matched"] == 0
    assert proj.summary["missing_position"] == 1


def test_two_positions_same_ld_resolve_independently():
    roster = build_house_roster([_sponsor(100, 5, "Rivers"), _sponsor(101, 5, "Chase")])
    filings = build_house_filings([_filing(5, 1, "Ann Rivers"), _filing(5, 2, "Ann Chase")])

    proj = build_house_seat_observations(roster, filings, biennium=BIENNIUM)

    assert set(proj.observations) == {
        Observation("100", KIND_HOUSE, "ld-5-position-1", BIENNIUM),
        Observation("101", KIND_HOUSE, "ld-5-position-2", BIENNIUM),
    }
    assert proj.summary["matched"] == 2


def test_shared_surname_broken_by_party():
    # Two same-surname members in one LD, one per position; party disambiguates each.
    roster = build_house_roster(
        [
            _sponsor(100, 5, "Smith", party="Democrat"),
            _sponsor(101, 5, "Smith", party="Republican"),
        ]
    )
    filings = build_house_filings(
        [
            _filing(5, 1, "Al Smith", party="(Prefers Democratic Party)"),
            _filing(5, 2, "Bo Smith", party="(Prefers Republican Party)"),
        ]
    )

    proj = build_house_seat_observations(roster, filings, biennium=BIENNIUM)

    assert set(proj.observations) == {
        Observation("100", KIND_HOUSE, "ld-5-position-1", BIENNIUM),
        Observation("101", KIND_HOUSE, "ld-5-position-2", BIENNIUM),
    }


def test_lone_unmatched_member_takes_the_remaining_position_by_elimination():
    """#103: the chamber seats exactly 2 members/LD, so when one seat is ballot-claimed and
    exactly one sitting member is unmatched, the remaining position is theirs by elimination.
    Covers both unmatched shapes — a mid-biennium appointee never on the ballot (Obras LD33) and
    a ballot↔roster name change (Caldier→Valdez LD26); the projector can't and needn't tell them
    apart. The inference is tracked (inferred_keys + summary), not folded into matched."""
    roster = build_house_roster([_sponsor(100, 33, "Gregerson"), _sponsor(101, 33, "Obras")])
    # The 2024 ballot: Gregerson won Pos 2; Pos 1's winner (Orwall) departed → blanked out of the
    # roster. Obras (her appointed successor) appears on no ballot line.
    filings = build_house_filings([_filing(33, 2, "Mia Gregerson"), _filing(33, 1, "Tina Orwall")])

    proj = build_house_seat_observations(roster, filings, biennium=BIENNIUM)

    assert set(proj.observations) == {
        Observation("100", KIND_HOUSE, "ld-33-position-2", BIENNIUM),
        Observation("101", KIND_HOUSE, "ld-33-position-1", BIENNIUM),
    }
    assert proj.inferred_keys == [("101", BIENNIUM)]
    assert proj.summary["matched"] == 1
    assert proj.summary["inferred"] == 1
    assert proj.summary["missing_position"] == 0


def test_elimination_declines_when_a_named_predecessor_still_claims_the_position():
    """Historical wires keep a mid-biennium predecessor NAMED (3 sitting members in the LD), so
    both positions stay ballot-claimed — the successor is honestly unpositioned (sequential
    occupancy, e.g. LD46 2017-18 Farrell→J. Valdez). Elimination must not paper over it."""
    roster = build_house_roster(
        [_sponsor(100, 46, "Pollet"), _sponsor(101, 46, "Farrell"), _sponsor(102, 46, "Valdez")]
    )
    filings = build_house_filings(
        [_filing(46, 1, "Gerry Pollet"), _filing(46, 2, "Jessyn Farrell")]
    )

    proj = build_house_seat_observations(roster, filings, biennium=BIENNIUM)

    assert proj.inferred_keys == []
    assert not any(o.member_id == "102" for o in proj.observations)
    assert proj.summary["matched"] == 2
    assert proj.summary["inferred"] == 0
    assert proj.summary["missing_position"] == 1


def test_elimination_declines_when_both_members_are_unmatched():
    """No seat is ballot-claimed (a double turnover, or a pre-2008 biennium below the SOS floor)
    → there is no single 'remaining' position to assign; both stay unpositioned."""
    roster = build_house_roster([_sponsor(100, 30, "Gregory"), _sponsor(101, 30, "Hickel")])
    filings = build_house_filings([])

    proj = build_house_seat_observations(roster, filings, biennium=BIENNIUM)

    assert proj.observations == [] and proj.inferred_keys == []
    assert proj.summary["inferred"] == 0
    assert proj.summary["missing_position"] == 2
