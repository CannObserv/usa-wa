"""A House→Senate move inside one biennium, dated from the Senate seating (usa-wa#490).

WSL lists a mid-biennium mover in BOTH chambers for the biennium they switch in,
and the #105 (a) mover exclusion drops their House row so the #103 elimination
can seat the appointed successor. Nothing then carried their House tenure into
that biennium: June Robinson's LD-38 Pos. 1 span ended 2018-12-31, not on her
2020-05-13 Senate appointment, and her House committee spans ran on to
2020-12-31. These pin the pure dating; the House and span families' own tests
pin the wiring.
"""

from dataclasses import replace
from datetime import date

from clearinghouse_domain_legislative.operator_overlay import SuccessionEvent
from clearinghouse_domain_legislative.span_kinds import KIND_COMMITTEE, KIND_HOUSE, KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import Observation, TenureSpan
from usa_wa_common.ballot import HousePosition
from usa_wa_pipeline.conformed.movers import (
    ChamberMove,
    carry_mover_house_tenures,
    chamber_moves,
    committee_agencies,
    date_mover_committees,
)

PRIOR = "2017-18"
MOVE_BIENNIUM = "2019-20"
SEAT = "ld-38-position-1"
SEATED_SENATE = date(2020, 5, 13)


def _wire(member_id: str, agency: str, last: str = "Robinson", district: str = "38") -> dict:
    return {"Id": member_id, "Agency": agency, "LastName": last, "District": district}


def _mover_wires() -> dict[str, list[dict]]:
    """The 2019-20 wire as WSL ships it: the mover's House row AND her Senate row."""
    return {
        MOVE_BIENNIUM: [
            _wire("18265", "House"),
            _wire("18265", "Senate"),
            _wire("17373", "House", last="Wicks"),
        ]
    }


def _seated_senate(member_id: str = "18265", on: date = SEATED_SENATE) -> SuccessionEvent:
    return SuccessionEvent(member_id, "seated", on, KIND_SENATE, "38")


def _vacated_house(member_id: str = "18265", on: date = SEATED_SENATE, seat: str = SEAT):
    return SuccessionEvent(member_id, "vacated", on, KIND_HOUSE, seat)


def _seated_house(member_id: str, on: date, seat: str = SEAT) -> SuccessionEvent:
    return SuccessionEvent(member_id, "seated", on, KIND_HOUSE, seat)


MOVE = ChamberMove(
    member_id="18265",
    biennium=MOVE_BIENNIUM,
    moved_on=SEATED_SENATE,
    ld=38,
    ld_peers=frozenset({"17373"}),
)


# --- chamber_moves ---------------------------------------------------------------


def test_a_movers_senate_seating_dates_the_move() -> None:
    """The Senate seating is the instant the House tenure ended — the roster states
    the move on both rows ("Resigned; Appntd to the Senate" / "Appointed May 13,
    2020"), and the seating carries the date."""
    assert chamber_moves(_mover_wires(), [_seated_senate()]) == [MOVE]


def test_the_move_biennium_ballot_positions_the_mover() -> None:
    """The November that seated the move biennium is the Position's own evidence: the
    mover is out of the roster, so the elimination never consulted it for them."""
    ballot = {MOVE_BIENNIUM: {38: [HousePosition("Position 2", frozenset({"robinson"}), "d")]}}
    [move] = chamber_moves(_mover_wires(), [_seated_senate()], positions_by_biennium=ballot)
    assert move.ballot_seat == "ld-38-position-2"


def test_the_earliest_senate_seating_dates_the_move() -> None:
    later = _seated_senate(on=date(2020, 9, 1))
    assert chamber_moves(_mover_wires(), [later, _seated_senate()]) == [MOVE]


def test_a_mover_with_no_dated_move_is_not_guessed(caplog) -> None:
    """No event, no date: the biennium floor is not when anybody moved."""
    caplog.set_level("INFO")
    assert chamber_moves(_mover_wires(), []) == []
    assert "chamber_move_undated" in caplog.text


def test_a_seating_in_another_biennium_dates_no_move() -> None:
    """A Senate seating dates only the biennium it falls in."""
    assert chamber_moves(_mover_wires(), [_seated_senate(on=date(2021, 1, 11))]) == []


def test_a_senator_who_never_sat_in_the_house_has_no_move() -> None:
    """The mover signal is the wire's own: a House row and a Senate row under one Id."""
    wires = {MOVE_BIENNIUM: [_wire("18265", "Senate")]}
    assert chamber_moves(wires, [_seated_senate()]) == []


def test_an_explicit_house_vacate_dates_a_move_with_no_senate_seating() -> None:
    """The #145 recipe's `vacated chamber-house` is the House half of the same move."""
    assert chamber_moves(_mover_wires(), [_vacated_house()]) == [MOVE]


def test_the_senate_seating_wins_over_a_house_vacate() -> None:
    """Both halves recorded: the move is the Senate seating, the House end stays the
    operator's own vacate."""
    events = [_vacated_house(on=date(2020, 5, 1)), _seated_senate()]
    assert chamber_moves(_mover_wires(), events) == [MOVE]


# --- carry_mover_house_tenures ---------------------------------------------------


def _prior_observation(member_id: str = "18265", seat: str = SEAT) -> Observation:
    return Observation(member_id, KIND_HOUSE, seat, PRIOR)


def test_the_house_tenure_carries_into_the_move_biennium_and_closes_on_the_move() -> None:
    """Carried as an observation, so `build_tenure_spans` merges it into the tenure
    it continues (one span, its key unchanged), and closed by an implied vacate."""
    observations, implied = carry_mover_house_tenures([_prior_observation()], [MOVE], [])
    assert Observation("18265", KIND_HOUSE, SEAT, MOVE_BIENNIUM) in observations
    assert implied == [_vacated_house()]


def test_a_successors_earlier_seating_ends_the_house_tenure() -> None:
    """The seat holds one person at a time: a successor seated before the Senate
    seating proves the vacancy came first (Liias, House successor appointed
    2014-01-21, Senate seating 2014-01-22). Ending at the Senate date would publish
    a one-day both-dated overlap the #360 clip must decline."""
    successor = _seated_house("17373", date(2020, 5, 12))
    _, implied = carry_mover_house_tenures([_prior_observation()], [MOVE], [successor])
    assert implied == [_vacated_house(on=date(2020, 5, 12))]


def test_a_successor_seated_on_the_floor_does_not_end_the_tenure() -> None:
    """A seating on the biennium's first day is a fresh term, not a mid-term vacancy."""
    successor = _seated_house("17373", date(2019, 1, 1))
    _, implied = carry_mover_house_tenures([_prior_observation()], [MOVE], [successor])
    assert implied == [_vacated_house()]


def test_an_explicit_vacate_needs_no_implied_one() -> None:
    """The operator already dated the House end; the carry only merges the tenure."""
    observations, implied = carry_mover_house_tenures(
        [_prior_observation()], [MOVE], [_vacated_house()]
    )
    assert Observation("18265", KIND_HOUSE, SEAT, MOVE_BIENNIUM) in observations
    assert implied == []


def test_an_explicit_vacate_of_another_seat_is_left_to_the_overlay() -> None:
    """The operator named a different seat than the one carried: the #145 synthesis
    owns that tenure, so nothing is carried that could contradict it."""
    other = _vacated_house(seat="ld-38-position-2")
    observations, implied = carry_mover_house_tenures([_prior_observation()], [MOVE], [other])
    assert observations == [_prior_observation()]
    assert implied == []


def test_a_mover_with_no_prior_house_seat_and_no_ballot_carries_nothing() -> None:
    """Appointed into the House in the move biennium (O'Ban): no tenure to continue and
    no ballot naming them — the #145 vacate synthesis is that case's."""
    assert carry_mover_house_tenures([], [MOVE], []) == ([], [])


def test_the_ballot_seat_wins_over_the_prior_seat() -> None:
    """A Position the move biennium's ballot names is evidence; the prior biennium's
    is an assumption that the seat did not change."""
    move = replace(MOVE, ballot_seat="ld-38-position-2")
    observations, implied = carry_mover_house_tenures([_prior_observation()], [move], [])
    assert Observation("18265", KIND_HOUSE, "ld-38-position-2", MOVE_BIENNIUM) in observations
    assert implied == [_vacated_house(seat="ld-38-position-2")]


def test_a_ballot_positioned_mover_needs_no_prior_seat() -> None:
    """Elected in the November before the move biennium, moved within it."""
    move = replace(MOVE, ballot_seat=SEAT)
    observations, implied = carry_mover_house_tenures([], [move], [])
    assert observations == [Observation("18265", KIND_HOUSE, SEAT, MOVE_BIENNIUM)]
    assert implied == [_vacated_house()]


def test_no_prior_seat_is_assumed_across_a_redistricting_era(caplog) -> None:
    """The back-chain's own stop (#118/#140): a new map keeps the LD number, not the
    district, so a prior Position proves nothing in 2013-14 without a ballot."""
    caplog.set_level("INFO")
    move = ChamberMove("13546", "2013-14", date(2014, 1, 22), ld=21)
    prior = Observation("13546", KIND_HOUSE, "ld-21-position-2", "2011-12")
    assert carry_mover_house_tenures([prior], [move], []) == ([prior], [])
    assert "chamber_move_house_seat_unknown" in caplog.text


def test_no_prior_seat_is_assumed_in_another_district() -> None:
    """The mover's House row names the LD they sat in that biennium."""
    move = replace(MOVE, ld=39)
    assert carry_mover_house_tenures([_prior_observation()], [move], []) == (
        [_prior_observation()],
        [],
    )


# --- the successor's seating names the seat (usa-wa#492) -------------------------

#: Cheryl Pflug's shape: LD-5 in 2003-04, the first biennium with House Positions.
#: No 2002 ballot is staged and 2001-02 has no seat to continue, so nothing but the
#: successor's appointment says which Position Pflug left.
PFLUG = ChamberMove("3479", "2003-04", date(2004, 1, 5), ld=5, ld_peers=frozenset({"5160", "9710"}))
RODNE_SEAT = "ld-5-position-1"


def _pflug_vacated(on: date = date(2004, 1, 5)) -> SuccessionEvent:
    return SuccessionEvent("3479", "vacated", on, KIND_HOUSE, RODNE_SEAT)


def test_a_successors_seating_names_the_seat_of_an_unpositioned_mover() -> None:
    """Jay Rodne was appointed to LD-5 Pos. 1 on 2004-01-20, after Pflug went to the
    Senate: the seat a successor is appointed to is the one the mover left."""
    rodne = _seated_house("9710", date(2004, 1, 20), seat=RODNE_SEAT)
    observations, implied = carry_mover_house_tenures([], [PFLUG], [rodne])
    assert observations == [Observation("3479", KIND_HOUSE, RODNE_SEAT, "2003-04")]
    assert implied == [_pflug_vacated()]


def test_the_carried_log_names_what_positioned_the_seat(caplog) -> None:
    """A successor-positioned seat is an inference an operator may want to audit."""
    caplog.set_level("INFO")
    rodne = _seated_house("9710", date(2004, 1, 20), seat=RODNE_SEAT)
    carry_mover_house_tenures([_prior_observation()], [MOVE, PFLUG], [rodne])
    vias = [r.via for r in caplog.records if r.getMessage() == "chamber_move_house_tenure_carried"]
    assert vias == ["prior", "successor"]


def test_a_successor_seated_before_the_move_still_dates_the_end() -> None:
    """The successor names the seat AND, seated first, proves the vacancy came first."""
    early = _seated_house("9710", date(2004, 1, 2), seat=RODNE_SEAT)
    _, implied = carry_mover_house_tenures([], [PFLUG], [early])
    assert implied == [_pflug_vacated(on=date(2004, 1, 2))]


def test_a_seating_elsewhere_names_no_seat() -> None:
    """Another district, another biennium, or the biennium's first day (a fresh term,
    not a vacancy): none of them is the mover's successor."""
    elsewhere = [
        _seated_house("1", date(2004, 1, 20), seat="ld-6-position-1"),
        _seated_house("2", date(2005, 3, 1), seat=RODNE_SEAT),
        _seated_house("3", date(2003, 1, 1), seat=RODNE_SEAT),
    ]
    assert carry_mover_house_tenures([], [PFLUG], elsewhere) == ([], [])


def test_a_seat_the_operator_spelled_unparseably_is_skipped_not_fatal() -> None:
    """Operator events are hand-entered and nothing validates the seat's spelling on
    the way in: one stray value must not fail the nightly build."""
    stray = _seated_house("9710", date(2004, 1, 20), seat="LD5 Pos 1")
    assert carry_mover_house_tenures([], [PFLUG], [stray]) == ([], [])


def test_two_seats_filled_in_the_district_name_neither(caplog) -> None:
    """Both of the LD's Positions filled mid-biennium: which one was the mover's is not
    something the seatings say."""
    caplog.set_level("INFO")
    both = [
        _seated_house("9710", date(2004, 1, 20), seat=RODNE_SEAT),
        _seated_house("4", date(2004, 3, 1), seat="ld-5-position-2"),
    ]
    assert carry_mover_house_tenures([], [PFLUG], both) == ([], [])
    assert "chamber_move_house_seat_unknown" in caplog.text


def test_a_seat_someone_else_held_that_biennium_is_not_the_movers() -> None:
    """The seating filled a seat the ballot or back-chain already gives another member
    in the move biennium: their vacancy, not the mover's."""
    holder = Observation("5160", KIND_HOUSE, RODNE_SEAT, "2003-04")
    rodne = _seated_house("9710", date(2004, 1, 20), seat=RODNE_SEAT)
    assert carry_mover_house_tenures([holder], [PFLUG], [rodne]) == ([holder], [])


def test_the_successor_may_hold_the_seat_in_the_move_biennium() -> None:
    """The successor's own observation on the seat is the succession, not a rival."""
    successor = Observation("9710", KIND_HOUSE, RODNE_SEAT, "2003-04")
    rodne = _seated_house("9710", date(2004, 1, 20), seat=RODNE_SEAT)
    observations, _ = carry_mover_house_tenures([successor], [PFLUG], [rodne])
    assert Observation("3479", KIND_HOUSE, RODNE_SEAT, "2003-04") in observations


def test_an_unpositioned_peers_vacancy_makes_the_seating_ambiguous(caplog) -> None:
    """Anderson unpositioned and gone mid-biennium: Rodne's seating may fill THEIR seat,
    and with no observation of Anderson's the held-seat guard cannot tell."""
    caplog.set_level("INFO")
    rodne = _seated_house("9710", date(2004, 1, 20), seat=RODNE_SEAT)
    anderson_left = SuccessionEvent("5160", "departed", date(2004, 1, 10), None, None)
    assert carry_mover_house_tenures([], [PFLUG], [rodne, anderson_left]) == ([], [])
    assert "chamber_move_house_seat_unknown" in caplog.text


def test_a_peer_leaving_after_the_seating_is_not_its_vacancy() -> None:
    """Rodne was seated before Anderson left: a seat already filled is not the one a
    later exit opened, so the seating is still Pflug's successor's."""
    rodne = _seated_house("9710", date(2004, 1, 20), seat=RODNE_SEAT)
    anderson_left = SuccessionEvent("5160", "departed", date(2004, 11, 1), None, None)
    observations, _ = carry_mover_house_tenures([], [PFLUG], [rodne, anderson_left])
    assert Observation("3479", KIND_HOUSE, RODNE_SEAT, "2003-04") in observations


def test_a_positioned_peers_vacancy_is_the_held_seat_guards() -> None:
    """Anderson's seat is known, so the seating that is not on it is the mover's."""
    anderson = Observation("5160", KIND_HOUSE, "ld-5-position-2", "2003-04")
    rodne = _seated_house("9710", date(2004, 1, 20), seat=RODNE_SEAT)
    anderson_left = SuccessionEvent(
        "5160", "vacated", date(2004, 1, 10), KIND_HOUSE, "ld-5-position-2"
    )
    observations, _ = carry_mover_house_tenures([anderson], [PFLUG], [rodne, anderson_left])
    assert Observation("3479", KIND_HOUSE, RODNE_SEAT, "2003-04") in observations


def test_the_successor_leaving_later_is_no_rival_vacancy() -> None:
    """The successor is a peer on the wire too; their own later exit names nothing."""
    rodne = _seated_house("9710", date(2004, 1, 20), seat=RODNE_SEAT)
    rodne_left = SuccessionEvent("9710", "departed", date(2004, 11, 1), None, None)
    observations, _ = carry_mover_house_tenures([], [PFLUG], [rodne, rodne_left])
    assert Observation("3479", KIND_HOUSE, RODNE_SEAT, "2003-04") in observations


def test_a_seat_an_earlier_move_carried_is_held() -> None:
    """Two movers in one LD: the first carries Pos. 1 from the ballot, so the only
    seating recorded (on Pos. 1) is that move's successor, not the second's."""
    first = ChamberMove("100", "2003-04", date(2004, 1, 5), ld=5, ballot_seat=RODNE_SEAT)
    rodne = _seated_house("9710", date(2004, 1, 20), seat=RODNE_SEAT)
    observations, _ = carry_mover_house_tenures([], [first, PFLUG], [rodne])
    assert observations == [Observation("100", KIND_HOUSE, RODNE_SEAT, "2003-04")]


def test_a_movers_own_house_vacate_leaves_the_seat_to_the_overlay() -> None:
    """O'Ban's shape: the operator dated and named the House end, so the #145
    synthesis owns that tenure and no successor is consulted."""
    rodne = _seated_house("9710", date(2004, 1, 20), seat=RODNE_SEAT)
    assert carry_mover_house_tenures([], [PFLUG], [rodne, _pflug_vacated()]) == ([], [])


# --- date_mover_committees -------------------------------------------------------


def _committee(
    committee_id: str, *, start: str, end: str, frm: date, to: date | None, member="18265"
):
    return TenureSpan(
        member_id=member,
        kind=KIND_COMMITTEE,
        discriminator=committee_id,
        start_biennium=start,
        end_biennium=end,
        valid_from=frm,
        valid_to=to,
        is_active=to is None,
    )


AGENCIES = {
    (MOVE_BIENNIUM, "12167"): "House",
    (MOVE_BIENNIUM, "456"): "Senate",
    (MOVE_BIENNIUM, "36500"): "Joint",
    (PRIOR, "8221"): "House",
}


def test_house_committees_end_and_senate_committees_begin_on_the_move() -> None:
    house = _committee(
        "12167", start="2015-16", end=MOVE_BIENNIUM, frm=date(2015, 1, 1), to=date(2020, 12, 31)
    )
    senate = _committee("456", start=MOVE_BIENNIUM, end="2025-26", frm=date(2019, 1, 1), to=None)
    [closed, opened] = date_mover_committees([house, senate], [MOVE], AGENCIES, house_spans=[])
    assert closed.valid_to == SEATED_SENATE
    assert closed.is_active is False
    assert closed.start_biennium == "2015-16"  # the key survives the re-date
    assert opened.valid_from == SEATED_SENATE
    assert opened.start_biennium == MOVE_BIENNIUM
    assert opened.valid_to is None


def test_committees_the_move_does_not_touch_are_left_alone() -> None:
    """A Joint committee spans both chambers; a House committee that ended before the
    move biennium already ended; another member's committee is not theirs."""
    joint = _committee(
        "36500", start=MOVE_BIENNIUM, end=MOVE_BIENNIUM, frm=date(2019, 1, 1), to=date(2020, 12, 31)
    )
    earlier = _committee(
        "8221", start=PRIOR, end=PRIOR, frm=date(2017, 1, 1), to=date(2018, 12, 31)
    )
    stranger = _committee(
        "12167",
        start=MOVE_BIENNIUM,
        end=MOVE_BIENNIUM,
        frm=date(2019, 1, 1),
        to=date(2020, 12, 31),
        member="99",
    )
    spans = [joint, earlier, stranger]
    assert date_mover_committees(spans, [MOVE], AGENCIES, house_spans=[]) == spans


def test_house_committees_end_with_the_house_seat() -> None:
    """When a successor's seating ended the House seat first, the committees end with
    it — a representative's committee seat cannot outlast their House seat."""
    seat = TenureSpan(
        "18265",
        KIND_HOUSE,
        SEAT,
        "2013-14",
        MOVE_BIENNIUM,
        date(2013, 12, 16),
        date(2020, 5, 12),
        False,
    )
    house = _committee(
        "12167", start="2015-16", end=MOVE_BIENNIUM, frm=date(2015, 1, 1), to=date(2020, 12, 31)
    )
    [closed] = date_mover_committees([house], [MOVE], AGENCIES, house_spans=[seat])
    assert closed.valid_to == date(2020, 5, 12)


def test_committee_agencies_key_on_biennium_and_committee() -> None:
    rows = [
        {"biennium": MOVE_BIENNIUM, "committee_id": "12167", "committee_agency": "House"},
        {"biennium": MOVE_BIENNIUM, "committee_id": "456", "committee_agency": "Senate"},
        {"biennium": None, "committee_id": "1", "committee_agency": "House"},
    ]
    assert committee_agencies(rows) == {
        (MOVE_BIENNIUM, "12167"): "House",
        (MOVE_BIENNIUM, "456"): "Senate",
    }


def test_house_committees_end_with_the_last_house_seat_held() -> None:
    """Two dated House ends in one biennium (a seat change, then the move): the
    committees outlast the first seat, so they end with the later one, whatever
    order the family lists them in."""
    first = TenureSpan(
        "18265",
        KIND_HOUSE,
        SEAT,
        "2017-18",
        MOVE_BIENNIUM,
        date(2017, 1, 1),
        date(2019, 3, 1),
        False,
    )
    second = TenureSpan(
        "18265",
        KIND_HOUSE,
        "ld-38-position-2",
        MOVE_BIENNIUM,
        MOVE_BIENNIUM,
        date(2019, 3, 1),
        date(2020, 5, 12),
        False,
    )
    house = _committee(
        "12167", start="2015-16", end=MOVE_BIENNIUM, frm=date(2015, 1, 1), to=date(2020, 12, 31)
    )
    for order in ([first, second], [second, first]):
        [closed] = date_mover_committees([house], [MOVE], AGENCIES, house_spans=order)
        assert closed.valid_to == date(2020, 5, 12)


def test_a_house_seats_quantized_ceiling_is_not_its_end() -> None:
    """A House span running to the biennium's Dec 31 was never dated there; the
    committees end at the move instead."""
    seat = TenureSpan(
        "18265",
        KIND_HOUSE,
        SEAT,
        "2017-18",
        MOVE_BIENNIUM,
        date(2017, 1, 1),
        date(2020, 12, 31),
        False,
    )
    house = _committee(
        "12167", start="2015-16", end=MOVE_BIENNIUM, frm=date(2015, 1, 1), to=date(2020, 12, 31)
    )
    [closed] = date_mover_committees([house], [MOVE], AGENCIES, house_spans=[seat])
    assert closed.valid_to == SEATED_SENATE


def test_an_open_house_seat_is_not_a_dated_end() -> None:
    """A current-biennium seat still open has no end to read; the committees end at
    the move rather than staying open beside a Senate seat."""
    seat = TenureSpan(
        "18265", KIND_HOUSE, SEAT, "2017-18", MOVE_BIENNIUM, date(2017, 1, 1), None, True
    )
    house = _committee("12167", start="2015-16", end=MOVE_BIENNIUM, frm=date(2015, 1, 1), to=None)
    [closed] = date_mover_committees([house], [MOVE], AGENCIES, house_spans=[seat])
    assert closed.valid_to == SEATED_SENATE
    assert closed.is_active is False
