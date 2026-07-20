"""Pure PDC↔WSL roster matching primitives (#79).

Direct unit coverage of the within-LD match cascade that seats a PDC winner onto a WSL member —
including the **party tiebreak** for a shared-surname LD and the "leave unresolved, never guess"
paths. These back the observation projector but are exercised here in isolation so the tricky
branches (shared surname split by party; ambiguous → ``None``) are pinned directly.
"""

from __future__ import annotations

from usa_wa_adapter_pdc.normalize.pdc_matching import (
    build_house_roster,
    build_senate_roster,
    find_confirming_senator,
    match_house_member,
)
from usa_wa_adapter_pdc.normalize.positions import surname_match_set

from usa_wa_adapter_legislature.normalize.members import canonicalize_party

R = canonicalize_party("R")
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


def test_build_senate_roster_groups_by_ld_and_skips_non_senate():
    roster = build_senate_roster(
        [
            _sponsor(100, 1, "Stanford", agency="Senate"),
            _sponsor(200, 1, "Rivers"),  # House — skipped
        ]
    )
    assert set(roster) == {1}
    assert roster[1][0].member_id == "100"


def test_build_senate_roster_skips_unparseable_rows():
    roster = build_senate_roster(
        [
            _sponsor(100, 1, "", agency="Senate"),  # blank last
            {"Id": 200, "LastName": "Stanford", "District": "", "Agency": "Senate"},  # blank LD
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


# --- match_house_member --------------------------------------------------------------------


def test_match_unique_surname():
    roster = build_house_roster([_sponsor(100, 5, "Rivers"), _sponsor(200, 5, "Barkis", party="R")])
    match = match_house_member(roster, 5, surname_match_set("Ann Rivers"), D)
    assert match is not None and match.member_id == "100"


def test_match_shared_surname_resolved_by_party():
    """Two 'Johnson' winners in one LD — resolved by the winner's party (the tiebreak branch)."""
    roster = build_house_roster(
        [_sponsor(100, 5, "Johnson", party="D"), _sponsor(200, 5, "Johnson", party="R")]
    )
    match = match_house_member(roster, 5, surname_match_set("Chris Johnson"), R)
    assert match is not None and match.member_id == "200"  # the Republican Johnson


def test_match_shared_surname_same_party_is_ambiguous():
    """Shared surname AND shared party → cannot disambiguate → None (never guessed)."""
    roster = build_house_roster(
        [_sponsor(100, 5, "Johnson", party="D"), _sponsor(200, 5, "Johnson", party="D")]
    )
    assert match_house_member(roster, 5, surname_match_set("Chris Johnson"), D) is None


def test_match_multiple_candidates_without_winner_party_is_none():
    roster = build_house_roster(
        [_sponsor(100, 5, "Johnson", party="D"), _sponsor(200, 5, "Johnson", party="R")]
    )
    assert match_house_member(roster, 5, surname_match_set("Chris Johnson"), None) is None


def test_match_no_surname_match_is_none():
    roster = build_house_roster([_sponsor(100, 5, "Rivers")])
    assert match_house_member(roster, 5, surname_match_set("Ghost Candidate"), D) is None


# --- find_confirming_senator ---------------------------------------------------------------


def test_confirming_senator_unique_match():
    senate = build_senate_roster([_sponsor(100, 5, "Rivers", agency="Senate")])
    senator = find_confirming_senator("Ann Rivers", 5, senate)
    assert senator is not None and senator.member_id == "100"


def test_confirming_senator_ambiguous_is_none():
    senate = build_senate_roster(
        [
            _sponsor(100, 5, "Rivers", agency="Senate"),
            _sponsor(101, 5, "Rivers", agency="Senate"),
        ]
    )
    assert find_confirming_senator("Ann Rivers", 5, senate) is None


def test_confirming_senator_no_match_is_none():
    senate = build_senate_roster([_sponsor(100, 5, "Rivers", agency="Senate")])
    assert find_confirming_senator("Somebody Else", 5, senate) is None
