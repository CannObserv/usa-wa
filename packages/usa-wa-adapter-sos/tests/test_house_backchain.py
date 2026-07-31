"""Unit tests for the pure #118 back-chain orchestrator.

A WA rep holds a specific Position continuously, so a ballot-anchored Position propagates
**backward** through an uninterrupted same-LD tenure (the direct seed), and seeding one seat in
an LD lets the #103 elimination resolve the mate (the 1-hop within-biennium cascade). The walk
stops at redistricting era boundaries, at an LD move / tenure gap (via the roster), and at the
``max_hops`` cap.
"""

from __future__ import annotations

from usa_wa_adapter_pdc.normalize.pdc_matching import build_house_roster
from usa_wa_adapter_pdc.normalize.pdc_observations import KIND_HOUSE
from usa_wa_adapter_sos.filings.normalize import build_house_filings
from usa_wa_adapter_sos.house.backchain import (
    REDISTRICTING_ERA_START_BIENNIA,
    backchain_house_observations,
)

from usa_wa_adapter_legislature.tenure_spans import Observation

# 2001-map era bienniums (no redistricting break between them); 2003-04 is the era floor.
ERA_2001 = ["2003-04", "2005-06", "2007-08", "2009-10"]


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


def _rosters(spec):
    return {b: build_house_roster(members) for b, members in spec.items()}


def _positions(spec):
    return {b: build_house_filings(filings) for b, filings in spec.items()}


def test_2003_04_is_a_redistricting_era_start():
    # Guards the constant the era-break logic rests on.
    assert "2003-04" in REDISTRICTING_ERA_START_BIENNIA
    assert "2013-14" in REDISTRICTING_ERA_START_BIENNIA
    assert "2005-06" not in REDISTRICTING_ERA_START_BIENNIA


def test_direct_seed_and_one_hop_elimination_cascade_back_through_the_era():
    """Rivers (M100) is ballot-anchored Pos 1 in 2009-10; her mate Chase (M101) is only
    elimination-seated (the balloted Pos-2 winner departed). Rivers' Pos 1 back-chains through
    2007-08→2003-04 (direct seed), and each biennium the seed lets #103 eliminate Chase into
    Pos 2 (the 1-hop cascade). The chain stops at the 2003-04 era floor (no 2001-02 seat)."""
    ld5 = [_sponsor(100, 5, "Rivers"), _sponsor(101, 5, "Chase")]
    rosters = _rosters({b: ld5 for b in ["2001-02", *ERA_2001]})
    # 2009-10 ballot names only Rivers (Pos 1); Pos 2's balloted winner departed.
    positions = _positions({"2009-10": [_filing(5, 1, "Ann Rivers")]})

    result = backchain_house_observations(rosters, positions, max_hops=4)

    obs = set(result.observations)
    for b in ERA_2001:
        assert Observation("100", KIND_HOUSE, "ld-5-position-1", b) in obs
        assert Observation("101", KIND_HOUSE, "ld-5-position-2", b) in obs
    # Era floor: nothing seated in 2001-02 (2003-04 is a redistricting era start).
    assert not any(o.biennium == "2001-02" for o in result.observations)
    # Rivers' pre-anchor seats are back-chained; Chase's are elimination (not back-chain).
    assert set(result.backchain_keys) == {
        ("100", "2007-08"),
        ("100", "2005-06"),
        ("100", "2003-04"),
    }
    assert ("101", "2007-08") not in result.backchain_keys
    # Depth decays with distance from the 2009-10 ballot anchor.
    assert result.depth[("100", "2009-10")] == 0
    assert result.depth[("100", "2007-08")] == 1
    assert result.depth[("100", "2005-06")] == 2
    assert result.depth[("100", "2003-04")] == 3


def test_max_hops_caps_the_back_chain_depth():
    """max_hops=1 seeds only one biennium before the anchor; deeper biennia stay unseated
    (both members unmatched → no elimination)."""
    ld5 = [_sponsor(100, 5, "Rivers"), _sponsor(101, 5, "Chase")]
    rosters = _rosters({b: ld5 for b in ERA_2001})
    positions = _positions({"2009-10": [_filing(5, 1, "Ann Rivers")]})

    result = backchain_house_observations(rosters, positions, max_hops=1)

    assert ("100", "2007-08") in result.backchain_keys  # hop 1 seeded
    assert not any(o.biennium == "2005-06" for o in result.observations)  # hop 2 capped
    assert not any(o.biennium == "2003-04" for o in result.observations)


def test_an_ld_move_breaks_the_chain_at_the_roster():
    """A back-chain seed keyed to the anchor's LD is not applied when the member sits a
    different LD that biennium — the roster breaks the chain (the 3 multi-LD movers)."""
    rosters = _rosters(
        {
            "2009-10": [_sponsor(100, 5, "Rivers"), _sponsor(101, 5, "Chase")],
            "2007-08": [_sponsor(100, 5, "Rivers"), _sponsor(101, 5, "Chase")],
            "2005-06": [_sponsor(100, 6, "Rivers"), _sponsor(101, 6, "Chase")],  # moved to LD6
        }
    )
    positions = _positions({"2009-10": [_filing(5, 1, "Ann Rivers")]})

    result = backchain_house_observations(rosters, positions, max_hops=4)

    assert Observation("100", KIND_HOUSE, "ld-5-position-1", "2007-08") in result.observations
    assert not any(o.member_id == "100" and o.biennium == "2005-06" for o in result.observations)


def test_a_tenure_gap_breaks_the_chain():
    """Rivers is absent from the 2007-08 roster (a tenure gap), so nothing seeds through it and
    her later presence in 2005-06 is not back-chained (a gap is a real tenure break)."""
    rosters = _rosters(
        {
            "2009-10": [_sponsor(100, 5, "Rivers"), _sponsor(101, 5, "Chase")],
            "2007-08": [_sponsor(101, 5, "Chase")],  # Rivers absent
            "2005-06": [_sponsor(100, 5, "Rivers"), _sponsor(101, 5, "Chase")],
        }
    )
    positions = _positions({"2009-10": [_filing(5, 1, "Ann Rivers")]})

    result = backchain_house_observations(rosters, positions, max_hops=4)

    assert not any(o.member_id == "100" and o.biennium == "2005-06" for o in result.observations)


def test_elimination_only_mates_do_not_carry_back_phase1():
    """Phase 1: a member resolved ONLY by within-biennium elimination is emitted but is not a
    carry-back source — the deeper recursive cascade (their own tenure) is Phase 2. Here Chase
    is elimination-seated in 2007-08; she must not seed her own earlier biennia beyond what
    Rivers' chain already reaches. Restated: no back-chain key is ever Chase's."""
    ld5 = [_sponsor(100, 5, "Rivers"), _sponsor(101, 5, "Chase")]
    rosters = _rosters({b: ld5 for b in ERA_2001})
    positions = _positions({"2009-10": [_filing(5, 1, "Ann Rivers")]})

    result = backchain_house_observations(rosters, positions, max_hops=4)

    assert all(member != "101" for member, _ in result.backchain_keys)
