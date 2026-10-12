"""The House Position span family as a stateless transform (#309 part 2, inc 3).

WSL owns *who sits* (the sponsor roster: LD + party); SOS owns *which position*
(the ballot's Position 1/2). The join, the #103 within-LD elimination and the
#118 back-chain are imported unchanged from `usa_wa_facts_seats.house` — these
pin the plumbing that feeds them from staging, and the guard that refuses to
publish a family whose ballot input went missing.
"""

from datetime import date

import pytest

from clearinghouse_domain_legislative.operator_overlay import SuccessionEvent
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


def test_a_departure_the_house_overlay_closes_reaches_the_family_tally() -> None:
    """usa-wa#466: `build_all_spans` reports a departure miss only when no overlay
    applied it, so the House overlay must add what it closed to the tally it is
    handed — a representative off every committee is closed here or nowhere."""
    departure = SuccessionEvent("100", "departed", date(2010, 6, 1))
    applied: set[SuccessionEvent] = set()
    [span] = build_house_spans(
        sponsors=[_sponsor("100", BIENNIUM, "5", "Rivera")],
        committee_members=[],
        sos_results=[_result("2008", 5, 1, "Pat Rivera")],
        events=[departure],
        current_biennium=CURRENT,
        applied_departures=applied,
    )
    assert span.valid_to == date(2010, 6, 1)
    assert applied == {departure}


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
    the 10% threshold — so nothing downstream would notice the seats vanish.
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


def test_the_next_seating_ballot_positions_nobody_before_the_rollover() -> None:
    """#135: from November the SOS harvest captures the ballot that seats 2027-28, and that
    biennium has no sponsor roster until the rollover. The ballot must not reach 2025-26:
    neither re-positioning an incumbent it lists in another Position nor, via the #118
    back-chain, seating a member the 2024 ballot never positioned."""
    sponsors = [
        _sponsor("100", CURRENT, "5", "Rivera"),
        _sponsor("200", CURRENT, "9", "Vance"),
    ]
    seated_2024 = [_result("2024", 5, 1, "Pat Rivera")]
    ballot_2026 = [_result("2026", 5, 2, "Pat Rivera"), _result("2026", 9, 1, "Chris Vance")]

    def spans(results: list[dict]) -> list:
        return build_house_spans(
            sponsors=sponsors,
            committee_members=[],
            sos_results=results,
            events=[],
            current_biennium=CURRENT,
        )

    before = spans(seated_2024)
    assert [(s.member_id, s.discriminator) for s in before] == [("100", "ld-5-position-1")]
    assert spans(seated_2024 + ballot_2026) == before


def _robinson_ld38(events: list[SuccessionEvent], *, current: str = CURRENT) -> list:
    """LD-38 across June Robinson's 2020 move, as WSL ships it: she keeps a House row
    in 2019-20 beside her Senate row, and Wicks (appointed to her seat) is listed too."""
    return build_house_spans(
        sponsors=[
            _sponsor("18265", "2017-18", "38", "Robinson"),
            _sponsor("2", "2017-18", "38", "Sells"),
            _sponsor("18265", "2019-20", "38", "Robinson"),
            _sponsor("18265", "2019-20", "38", "Robinson", agency="Senate"),
            _sponsor("2", "2019-20", "38", "Sells"),
            _sponsor("17373", "2019-20", "38", "Wicks"),
        ],
        committee_members=[],
        sos_results=[
            _result("2016", 38, 1, "Pat Robinson"),
            _result("2016", 38, 2, "Pat Sells"),
            _result("2018", 38, 1, "Pat Robinson"),
            _result("2018", 38, 2, "Pat Sells"),
        ],
        events=events,
        current_biennium=current,
    )


ROBINSON_TO_SENATE = SuccessionEvent("18265", "seated", date(2020, 5, 13), "chamber-senate", "38")
WICKS_SEATED = SuccessionEvent("17373", "seated", date(2020, 5, 13), KIND_HOUSE, "ld-38-position-1")


def test_a_movers_house_tenure_runs_to_the_senate_seating() -> None:
    """usa-wa#490: the mover exclusion drops her 2019-20 House row so the elimination
    seats Wicks — and nothing carried her own tenure into 2019-20, so it ended
    2018-12-31. It continues, as ONE span under its original key, to the seating."""
    spans = _robinson_ld38([ROBINSON_TO_SENATE, WICKS_SEATED])
    by_member = {s.member_id: s for s in spans}
    robinson = by_member["18265"]
    assert (robinson.discriminator, robinson.start_biennium) == ("ld-38-position-1", "2017-18")
    assert robinson.valid_to == date(2020, 5, 13)
    assert robinson.is_active is False
    assert [s.member_id for s in spans].count("18265") == 1
    wicks = by_member["17373"]
    assert (wicks.discriminator, wicks.valid_from) == ("ld-38-position-1", date(2020, 5, 13))


def test_a_move_in_the_current_biennium_closes_the_open_house_tenure() -> None:
    """Alvarado's shape: the carried tenure reaches the current biennium, so it would
    publish OPEN — a sitting senator still holding her House seat — without the
    implied vacate closing it."""
    [robinson, *_] = [
        s for s in _robinson_ld38([ROBINSON_TO_SENATE], current="2019-20") if s.member_id == "18265"
    ]
    assert robinson.valid_to == date(2020, 5, 13)
    assert robinson.is_active is False


def test_an_undated_mover_keeps_the_prior_shape() -> None:
    """No Senate seating, no move date: her tenure ends where the wire last placed her."""
    [robinson] = [s for s in _robinson_ld38([]) if s.member_id == "18265"]
    assert robinson.valid_to == date(2018, 12, 31)


def test_a_2003_04_mover_takes_the_seat_their_successor_was_appointed_to() -> None:
    """usa-wa#492: 2003-04 is the first biennium with House Positions and no 2002 ballot
    is staged, so Cheryl Pflug had no seat to carry and published NO House span. Jay
    Rodne's appointment to LD-5 Pos. 1 names the seat Pflug left; the tenure runs from
    the floor to the Senate seating."""
    spans = build_house_spans(
        sponsors=[
            _sponsor("3479", "2003-04", "5", "Pflug"),
            _sponsor("3479", "2003-04", "5", "Pflug", agency="Senate"),
            _sponsor("5160", "2003-04", "5", "Anderson"),
            _sponsor("9710", "2003-04", "5", "Rodne"),
        ],
        committee_members=[],
        sos_results=[_result("2008", 5, 1, "Pat Rodne")],
        events=[
            SuccessionEvent("3479", "seated", date(2004, 1, 5), "chamber-senate", "5"),
            SuccessionEvent("9710", "seated", date(2004, 1, 20), KIND_HOUSE, "ld-5-position-1"),
        ],
        current_biennium=CURRENT,
    )
    by_member = {s.member_id: s for s in spans}
    pflug = by_member["3479"]
    assert (pflug.discriminator, pflug.start_biennium) == ("ld-5-position-1", "2003-04")
    assert (pflug.valid_from, pflug.valid_to) == (date(2003, 1, 1), date(2004, 1, 5))
    assert pflug.is_active is False
