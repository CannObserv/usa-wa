"""The WSL House roster builder (#79) and its chamber-mover exclusion (#105 (a), #145).

Direct unit coverage of ``build_house_roster`` — grouping by LD, skipping unparseable and
non-House rows, dropping a same-wire House→Senate mover and an excluded stale member — and of
``house_mover_ids``, the mover set the conformed House hands the overlay.
"""

from __future__ import annotations

from usa_wa_common.parties import canonicalize_party
from usa_wa_facts_seats.house.roster import (
    build_house_roster,
    house_mover_ids,
)

D = canonicalize_party("D")


def _sponsor(mid, ld, last, agency="House", party="D"):
    return {
        "Id": mid,
        "FirstName": "X",
        "LastName": last,
        "District": str(ld),
        "Agency": agency,
        "Party": party,
    }


# --- roster builders -----------------------------------------------------------------------


def test_build_house_roster_groups_by_ld_and_skips_non_house():
    roster = build_house_roster(
        [
            _sponsor(100, 5, "Rivers"),
            _sponsor(200, 5, "Barkis", party="R"),
            _sponsor(300, 8, "Stanford", agency="Senate"),  # Senate — skipped
        ]
    )
    assert set(roster) == {5}
    assert {e.member_id for e in roster[5]} == {"100", "200"}
    assert roster[5][0].party_slug == D


def test_build_house_roster_skips_unparseable_rows():
    """A blank surname or blank district can't seat a House member — dropped, not raised."""
    roster = build_house_roster(
        [
            _sponsor(100, 5, ""),  # blank last
            {"Id": 200, "LastName": "Rivers", "District": "", "Agency": "House"},  # blank district
        ]
    )
    assert roster == {}


def test_build_house_roster_excludes_same_wire_senate_mover():
    """#105 (a): a mid-biennium House→Senate mover keeps a named House row under the SAME
    stable Id as their Senate row (Alvarado 34024, Hunt 35410 — verified in the 2025-26 wire).
    The House row is stale — drop it so the LD reads 2-member and the #103 elimination can
    seat the real appointed replacement."""
    roster = build_house_roster(
        [
            _sponsor(100, 34, "Alvarado"),
            _sponsor(100, 34, "Alvarado", agency="Senate"),
            _sponsor(200, 34, "Fitzgibbon"),
        ]
    )
    assert {e.member_id for e in roster[34]} == {"200"}


def test_house_mover_ids_returns_the_excluded_movers():
    """#145: the mover set the overlay gates closed-span synthesis on — House rows whose stable
    Id also appears in a named Senate row (the same set build_house_roster drops). A non-mover
    House row and a Senate-only member are not movers."""
    members = [
        _sponsor(100, 34, "Alvarado"),  # House
        _sponsor(100, 34, "Alvarado", agency="Senate"),  # same Id in Senate → mover
        _sponsor(200, 34, "Fitzgibbon"),  # House only → not a mover
        _sponsor(300, 5, "Wilson", agency="Senate"),  # Senate only → not a mover
    ]
    assert house_mover_ids(members) == {"100"}


def test_house_mover_ids_ignores_name_blanked_senate_stub():
    """A name-blanked Senate stub (boundary mover, null LastName) is not a mover signal — matches
    build_house_roster's `LastName` guard so a stub can't spuriously flag a House row."""
    members = [
        _sponsor(100, 34, "Alvarado"),
        {"Id": 100, "LastName": "", "District": "", "Agency": "Senate"},  # blanked stub
    ]
    assert house_mover_ids(members) == set()


def test_build_house_roster_mover_exclusion_survives_ld_change():
    """Id-keyed, not within-LD: a mover whose Senate seat is a different LD still drops."""
    roster = build_house_roster(
        [_sponsor(100, 5, "Hunt"), _sponsor(100, 7, "Hunt", agency="Senate")]
    )
    assert 5 not in roster


def test_build_house_roster_exclude_ids_drops_stale_member():
    """#105 (b): a caller-supplied exclusion set (committee-corroborated stale members —
    Senn/Kilduff) removes the row; ids are matched as strings against the wire Id."""
    roster = build_house_roster(
        [_sponsor(100, 41, "Senn"), _sponsor(200, 41, "Thai")], exclude_ids={"100"}
    )
    assert {e.member_id for e in roster[41]} == {"200"}
