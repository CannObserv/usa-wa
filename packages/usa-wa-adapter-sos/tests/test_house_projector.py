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
