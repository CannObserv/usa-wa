"""results.vote.wa.gov → House-position primitives — the robust race-label parser (#101).

The audit found WA SOS labels the House office three ways (sometimes differing between the two
seats of one district in one file). These tests pin all three variants — using the exact real
edge cases (2020 LD15 Chandler/Dufault, 2014 LD30 Freeman) — plus Senate/other-office and WRITE-IN
exclusion and the GOP party synonym.
"""

from __future__ import annotations

from usa_wa_adapter_sos.results.normalize import (
    build_house_positions,
    build_house_winners,
    build_senate_winners,
    parse_house_race,
    parse_senate_race,
)
from usa_wa_common.ballot import position_for
from usa_wa_common.names import fold_token


def _row(
    race: str,
    candidate: str,
    party: str = "(Prefers Democratic Party)",
    votes: str = "100",
) -> dict[str, str]:
    return {"Race": race, "Candidate": candidate, "Party": party, "Votes": votes}


def test_parse_house_race_standard_case_insensitive() -> None:
    assert parse_house_race("LEGISLATIVE DISTRICT 1 - State Representative Pos. 1") == (
        1,
        "Position 1",
    )
    assert parse_house_race("Legislative District 10 - State Representative Pos. 2") == (
        10,
        "Position 2",
    )


def test_parse_house_race_variant_a_representative_position() -> None:
    # 2020 LD15 labelled its House seats "Representative, Position N" (no "State", spelled out).
    assert parse_house_race("LEGISLATIVE DISTRICT 15 - Representative, Position 1") == (
        15,
        "Position 1",
    )
    assert parse_house_race("LEGISLATIVE DISTRICT 15 - Representative, Position 2") == (
        15,
        "Position 2",
    )


def test_parse_house_race_variant_b_bare_trailing_digit() -> None:
    # 2014 LD30 Pos. 2 was a bare "State Representative 2" (no "Pos.").
    assert parse_house_race("Legislative District 30 - State Representative 2") == (
        30,
        "Position 2",
    )


def test_parse_house_race_senate_and_other_office_return_none() -> None:
    assert parse_house_race("LEGISLATIVE DISTRICT 1 - State Senator") is None
    assert parse_house_race("STATE GOVERNOR") is None
    assert parse_house_race("") is None


def test_build_house_positions_skips_write_in_and_senate() -> None:
    rows = [
        _row("LEGISLATIVE DISTRICT 1 - State Representative Pos. 1", "Davina Duerr"),
        _row("LEGISLATIVE DISTRICT 1 - State Representative Pos. 1", "WRITE-IN", " "),
        _row("LEGISLATIVE DISTRICT 1 - State Senator", "A Senator"),
    ]
    by_ld = build_house_positions(rows)
    assert set(by_ld) == {1}
    assert len(by_ld[1]) == 1  # the write-in row + the Senate row are excluded


def test_build_house_positions_resolves_the_audited_variant_seats() -> None:
    rows = [
        _row(
            "LEGISLATIVE DISTRICT 15 - Representative, Position 1",
            "Bruce Chandler",
            "(Prefers Republican Party)",
        ),
        _row("Legislative District 30 - State Representative 2", "Roger Freeman"),
    ]
    by_ld = build_house_positions(rows)
    # Real members an exact-match parser would have silently dropped.
    assert position_for(by_ld, 15, fold_token("Chandler"), "republican") == "Position 1"
    assert position_for(by_ld, 30, fold_token("Freeman"), "democratic") == "Position 2"


def test_parse_senate_race_is_the_house_parser_s_mirror() -> None:
    assert parse_senate_race("LEGISLATIVE DISTRICT 5 - State Senator") == 5
    assert parse_senate_race("Legislative District 33 - State Senator") == 33
    # a House contest is not a Senate one, and vice versa — the two parsers partition the wire
    assert parse_senate_race("LEGISLATIVE DISTRICT 5 - State Representative Pos. 1") is None
    assert parse_senate_race("STATE GOVERNOR") is None
    assert parse_senate_race("") is None


def test_build_senate_winners_picks_the_top_vote_candidacy() -> None:
    """The Senate rows the House parser drops (#106 A′): the wire carries every candidacy, so the
    winner is the top-vote non-write-in row. Real 2025 LD5 special — Hunt beat Magendanz."""
    rows = [
        _row("LEGISLATIVE DISTRICT 5 - State Senator", "Victoria Hunt", votes="28466"),
        _row(
            "LEGISLATIVE DISTRICT 5 - State Senator",
            "Chad Magendanz",
            "(Prefers Republican Party)",
            votes="22063",
        ),
        _row("LEGISLATIVE DISTRICT 5 - State Senator", "WRITE-IN", " ", votes="43"),
    ]
    winners = build_senate_winners(rows)

    assert set(winners) == {5}
    assert winners[5].ballot_name == "Victoria Hunt"
    assert fold_token("Hunt") in winners[5].name_keys
    assert winners[5].party_slug == "democratic" and winners[5].votes == 28466


def test_build_senate_winners_ignores_house_races() -> None:
    rows = [
        _row("Legislative District 48 - State Representative Pos. 1", "Osman S", votes="1"),
        _row("Legislative District 48 - State Senator", "Vandana Slatter", votes="16866"),
    ]
    assert {ld: w.ballot_name for ld, w in build_senate_winners(rows).items()} == {
        48: "Vandana Slatter"
    }


def test_build_senate_winners_declines_an_unresolvable_race() -> None:
    """Never guess (the ``position_for`` discipline): a tie or an unparseable vote count leaves the
    LD out rather than naming an arbitrary row as the winner. A single uncontested candidacy is
    unambiguous even with no vote counts."""
    tied = [
        _row("LEGISLATIVE DISTRICT 7 - State Senator", "A Candidate", votes="500"),
        _row("LEGISLATIVE DISTRICT 7 - State Senator", "B Candidate", votes="500"),
    ]
    assert build_senate_winners(tied) == {}

    unparseable = [
        _row("LEGISLATIVE DISTRICT 8 - State Senator", "A Candidate", votes=""),
        _row("LEGISLATIVE DISTRICT 8 - State Senator", "B Candidate", votes=""),
    ]
    assert build_senate_winners(unparseable) == {}

    # Mixed: a contested LD where only one row's count parses is untrustworthy — the blank could be
    # the real top — so it is omitted, not resolved to the single counted (possibly losing) row.
    mixed = [
        _row("LEGISLATIVE DISTRICT 10 - State Senator", "Counted", votes="500"),
        _row("LEGISLATIVE DISTRICT 10 - State Senator", "Blank", votes=""),
    ]
    assert build_senate_winners(mixed) == {}

    sole = [_row("LEGISLATIVE DISTRICT 9 - State Senator", "Sole Candidate", votes="")]
    assert build_senate_winners(sole)[9].ballot_name == "Sole Candidate"


def test_build_house_winners_keeps_only_the_top_vote_candidacy_per_seat() -> None:
    """The odd-year merge (#123 hazard b): the House map is candidacies incl. losers, so the odd
    cohort is filtered to the winning candidacy **per (LD, position) race** before it is unioned
    into the even seating map — else a losing special-election candidacy false-matches a member.
    Real 2025 LD33 P1 special: Osman Salahuddin won; a loser must not enter the map."""
    rows = [
        _row(
            "LEGISLATIVE DISTRICT 33 - State Representative Pos. 1",
            "Osman Salahuddin",
            votes="30000",
        ),
        _row(
            "LEGISLATIVE DISTRICT 33 - State Representative Pos. 1",
            "A Loser",
            "(Prefers Republican Party)",
            votes="12000",
        ),
        _row("LEGISLATIVE DISTRICT 33 - State Representative Pos. 1", "WRITE-IN", " ", votes="40"),
    ]
    by_ld = build_house_winners(rows)
    assert position_for(by_ld, 33, fold_token("Salahuddin"), "democratic") == "Position 1"
    # The loser is gone — only the winner's name resolves.
    assert position_for(by_ld, 33, fold_token("Loser"), "republican") is None
    assert len(by_ld[33]) == 1


def test_build_house_winners_ranks_each_position_independently() -> None:
    """The two seats of one LD are distinct races — each keeps its own top-vote winner."""
    rows = [
        _row("LEGISLATIVE DISTRICT 5 - State Representative Pos. 1", "Ada Ackerman", votes="100"),
        _row("LEGISLATIVE DISTRICT 5 - State Representative Pos. 1", "Bo Baker", votes="40"),
        _row("LEGISLATIVE DISTRICT 5 - State Representative Pos. 2", "Cy Carter", votes="90"),
        _row("LEGISLATIVE DISTRICT 5 - State Representative Pos. 2", "Di Dean", votes="80"),
    ]
    by_ld = build_house_winners(rows)
    assert {p.qualifier for p in by_ld[5]} == {"Position 1", "Position 2"}
    assert position_for(by_ld, 5, fold_token("Ackerman"), None) == "Position 1"
    assert position_for(by_ld, 5, fold_token("Carter"), None) == "Position 2"
    assert position_for(by_ld, 5, fold_token("Baker"), None) is None  # the P1 loser is gone


def test_build_house_winners_declines_ties_and_unparseable_and_keeps_sole() -> None:
    """Same never-guess discipline as ``build_senate_winners``: a tie or an untrustworthy count
    omits the race; a sole uncontested candidacy is unambiguous even with no votes."""
    tie = [
        _row("LEGISLATIVE DISTRICT 7 - State Representative Pos. 1", "A", votes="500"),
        _row("LEGISLATIVE DISTRICT 7 - State Representative Pos. 1", "B", votes="500"),
    ]
    assert build_house_winners(tie) == {}

    mixed = [
        _row("LEGISLATIVE DISTRICT 8 - State Representative Pos. 1", "Counted", votes="500"),
        _row("LEGISLATIVE DISTRICT 8 - State Representative Pos. 1", "Blank", votes=""),
    ]
    assert build_house_winners(mixed) == {}

    sole = [_row("LEGISLATIVE DISTRICT 9 - State Representative Pos. 2", "Sole", votes="")]
    assert build_house_winners(sole)[9][0].qualifier == "Position 2"

    # Senate rows and write-ins never enter the House winners map.
    assert build_house_winners([_row("LEGISLATIVE DISTRICT 1 - State Senator", "Sen")]) == {}


def test_build_house_positions_canonicalises_gop_party() -> None:
    rows = [
        _row(
            "LEGISLATIVE DISTRICT 2 - State Representative Pos. 1",
            "Andrew Barkis",
            "(Prefers GOP Party)",
        )
    ]
    by_ld = build_house_positions(rows)
    assert by_ld[2][0].party_slug == "republican"  # GOP folds so the party tiebreak still works
