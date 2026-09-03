"""The House Position span family as a stateless transform (#309 part 2, inc 3).

WSL owns *who sits* (the sponsor roster: LD + party); SOS owns *which position*
(the ballot's Position 1/2). The join, the #103 within-LD elimination and the
#118 back-chain are imported unchanged from `usa_wa_facts_seats.house` — these
pin the plumbing that feeds them from staging, and the guard that refuses to
publish a family whose ballot input went missing.
"""

import pytest

from clearinghouse_domain_legislative.span_kinds import KIND_HOUSE
from usa_wa_pipeline.conformed.house import (
    build_house_spans,
    house_positions_by_year,
    sos_result_wires,
)

CURRENT = "2025-26"
BIENNIUM = "2009-10"


def _sponsor(member_id: str, biennium: str, district: str, last: str, **over) -> dict:
    row = {
        "biennium": biennium,
        "member_id": member_id,
        "agency": "House",
        "name": f"Rep. {last}",
        "long_name": f"Representative {last}",
        "first_name": "Pat",
        "last_name": last,
        "party": "D",
        "district": district,
    }
    row.update(over)
    return row


def _result(year: str, district: int, position: int, candidate: str, **over) -> dict:
    row = {
        "election_date": f"{year}1103",
        "race": f"Legislative District {district} - State Representative Pos. {position}",
        "candidate": candidate,
        "party": "(Prefers Democratic Party)",
        "votes": "30000",
        "percentage_of_total_votes": "60.0",
        "jurisdiction_name": "Legislative",
    }
    row.update(over)
    return row


def test_sos_result_wires_restore_the_csv_header_shape() -> None:
    """The SOS normalizers read the archived CSV's own header keys (`Race`,
    `Candidate`, `Party`, `Votes`); staging carries the same facts lowercased.
    A dropped field is a silently unpositioned member."""
    wires = sos_result_wires([_result("2008", 1, 1, "Pat Rivera")])
    assert list(wires) == [2008]
    [wire] = wires[2008]
    assert wire["Race"] == "Legislative District 1 - State Representative Pos. 1"
    assert wire["Candidate"] == "Pat Rivera"
    assert wire["Party"] == "(Prefers Democratic Party)"
    assert wire["Votes"] == "30000"


def test_a_result_row_with_no_parseable_year_is_skipped() -> None:
    """`election_date` is the only thing keying a cohort to its election year;
    a row that cannot supply one cannot be attributed to a ballot."""
    assert sos_result_wires([_result("", 1, 1, "Pat Rivera", election_date="")]) == {}


def test_house_positions_split_seating_from_special_winners() -> None:
    """The even November seats the chamber (full candidacy set — the #103
    elimination needs the losers); the odd November's specials contribute
    WINNERS only, so a losing special candidacy cannot false-match (#123)."""
    positions, winners = house_positions_by_year(
        sos_result_wires([_result("2008", 5, 1, "Pat Rivera"), _result("2009", 5, 2, "Sam Cole")])
    )
    assert 5 in positions[2008]
    assert 5 in winners[2009]


def test_the_ballot_position_seats_a_house_member() -> None:
    """The join's whole point: a roster row carries the LD, the ballot carries
    the Position, and the span's discriminator is the seat."""
    spans = build_house_spans(
        sponsors=[_sponsor("100", BIENNIUM, "5", "Rivera")],
        committee_members=[],
        sos_results=[_result("2008", 5, 1, "Pat Rivera")],
        events=[],
        current_biennium=CURRENT,
    )
    [span] = spans
    assert span.kind == KIND_HOUSE
    assert span.member_id == "100"
    # the seat's own identity, as canonical spells it: ld-<n>-position-<p>
    assert span.discriminator == "ld-5-position-1"
    assert span.start_biennium == BIENNIUM


def test_a_member_the_ballot_cannot_position_gets_no_seat() -> None:
    """OQ1: a positioned seat's absence is honest. A position-less
    `state_representative` is not a fact — PM rejects it and so do we."""
    spans = build_house_spans(
        sponsors=[_sponsor("100", BIENNIUM, "5", "Rivera")],
        committee_members=[],
        sos_results=[_result("2008", 9, 1, "Chris Vance")],
        events=[],
        current_biennium=CURRENT,
    )
    assert spans == []


def test_a_senate_row_seats_nothing() -> None:
    spans = build_house_spans(
        sponsors=[_sponsor("100", BIENNIUM, "5", "Rivera", agency="Senate")],
        committee_members=[],
        sos_results=[_result("2008", 5, 1, "Pat Rivera")],
        events=[],
        current_biennium=CURRENT,
    )
    assert spans == []


def test_a_missing_ballot_archive_is_refused() -> None:
    """The same rule as the #228 deepening (CR 57): an input whose absence
    silently deletes a whole family must refuse, not return empty. The publish
    shrink gate cannot see it — chamber-house is ~4% of the table, well inside
    the 10% floor — so nothing downstream would notice the seats vanish.
    """
    with pytest.raises(ValueError, match="ballot"):
        build_house_spans(
            sponsors=[_sponsor("100", BIENNIUM, "5", "Rivera")],
            committee_members=[],
            sos_results=[],
            events=[],
            current_biennium=CURRENT,
        )


def test_an_empty_corpus_needs_no_ballot() -> None:
    """The refusal is about a ballot archive that went missing under a live
    roster, not about the hermetic build (empty raw root ⇒ no sponsors)."""
    assert (
        build_house_spans(
            sponsors=[],
            committee_members=[],
            sos_results=[],
            events=[],
            current_biennium=CURRENT,
        )
        == []
    )
