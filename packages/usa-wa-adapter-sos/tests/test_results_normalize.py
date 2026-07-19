"""results.vote.wa.gov → House-position primitives — the robust race-label parser (#101).

The audit found WA SOS labels the House office three ways (sometimes differing between the two
seats of one district in one file). These tests pin all three variants — using the exact real
edge cases (2020 LD15 Chandler/Dufault, 2014 LD30 Freeman) — plus Senate/other-office and WRITE-IN
exclusion and the GOP party synonym.
"""

from __future__ import annotations

from usa_wa_adapter_pdc.normalize.positions import fold_token
from usa_wa_adapter_sos.positions import position_for
from usa_wa_adapter_sos.results.normalize import build_house_positions, parse_house_race


def _row(race: str, candidate: str, party: str = "(Prefers Democratic Party)") -> dict[str, str]:
    return {"Race": race, "Candidate": candidate, "Party": party, "Votes": "100"}


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
