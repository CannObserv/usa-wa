"""A House→Senate move inside one biennium, dated from the Senate seating (usa-wa#490).

WSL lists a mid-biennium mover in BOTH chambers for the biennium they switch in,
and the #105 (a) mover exclusion drops their House row so the #103 elimination
can seat the appointed successor. Nothing then carried their House tenure into
that biennium: June Robinson's LD-38 Pos. 1 span ended 2018-12-31, not on her
2020-05-13 Senate appointment, and her House committee spans ran on to
2020-12-31. These pin the pure dating; the House and span families' own tests
pin the wiring.
"""

from datetime import date

from clearinghouse_domain_legislative.operator_overlay import SuccessionEvent
from clearinghouse_domain_legislative.span_kinds import KIND_COMMITTEE, KIND_HOUSE, KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import Observation, TenureSpan
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


MOVE = ChamberMove(member_id="18265", biennium=MOVE_BIENNIUM, moved_on=SEATED_SENATE)


# --- chamber_moves ---------------------------------------------------------------


def test_a_movers_senate_seating_dates_the_move() -> None:
    """The Senate seating is the instant the House tenure ended — the roster states
    the move on both rows ("Resigned; Appntd to the Senate" / "Appointed May 13,
    2020"), and the seating carries the date."""
    assert chamber_moves(_mover_wires(), [_seated_senate()]) == [MOVE]


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


def test_a_mover_with_no_prior_house_seat_carries_nothing() -> None:
    """Newly elected in the move biennium (Rivers, Frockt): there is no tenure to
    continue, and the ballot positioned nobody — the #145 vacate is that case's."""
    assert carry_mover_house_tenures([], [MOVE], []) == ([], [])


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
