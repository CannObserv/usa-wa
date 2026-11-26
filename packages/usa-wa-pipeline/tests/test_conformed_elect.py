"""Member-elect spans (#600 spec § 2): the seating general's winners, joined to the next
biennium's WSL listing, from election day to the day before the term opens."""

from __future__ import annotations

from collections import Counter
from datetime import date
from types import SimpleNamespace

import numpy as np

from clearinghouse_domain_legislative.boundary_basis import BASIS_DATED
from usa_wa_pipeline.conformed.elect import build_elect_spans

B = "2027-28"
DAY = "20261103"


def _sponsor(member_id, agency, last, party, district, biennium=B):
    return {
        "biennium": biennium,
        "member_id": member_id,
        "agency": agency,
        "name": last,
        "long_name": f"X {last}",
        "first_name": "X",
        "last_name": last,
        "party": party,
        "district": district,
    }


def _result(race, candidate, party, votes, day=DAY):
    return {
        "election_date": day,
        "race": race,
        "candidate": candidate,
        "party": party,
        "votes": str(votes),
    }


def _house(ld, pos):
    return f"Legislative District {ld} - State Representative Pos. {pos}"


def _senate(ld):
    return f"Legislative District {ld} - State Senator"


REP, DEM = "(Prefers Republican Party)", "(Prefers Democratic Party)"

RESULTS = [
    _result(_house(4, 1), "Hillary Q. Pham", REP, 29716),
    _result(_house(4, 1), "Trent Maier", REP, 26476),
    _result(_house(4, 1), "Write-in", "", 3979),
    _result(_house(4, 2), "Rob Chase", REP, 38814),
    _result(_house(4, 2), "Rob Tupper", DEM, 29527),
    _result(_senate(26), "Jesse Young", REP, 40000),
    _result(_senate(26), "Someone Else", DEM, 30000),
]
SPONSORS = [
    _sponsor("35476", "House", "Pham", "R", "4"),
    _sponsor("31521", "House", "Chase", "R", "4"),
    _sponsor("18458", "Senate", "Christian", "R", "4"),  # LD4's seat wasn't on the ballot
    _sponsor("28000", "Senate", "Young", "R", "26"),
]
FIRST_SEEN = [
    {"biennium": B, "member_id": m, "first_seen_at": "2026-11-25T08:03:42.414628Z"}
    for m in ("35476", "31521", "18458", "28000")
]


def _build(**over):
    kwargs = {
        "sponsors": SPONSORS,
        "sos_results": RESULTS,
        "first_seen": FIRST_SEEN,
        "elections": [],
        "events": [],
    }
    return {(s.member_id, s.kind): s for s in build_elect_spans(**{**kwargs, **over})}


def test_each_winner_wsl_lists_is_a_member_elect_from_election_day_to_the_term_eve():
    spans = _build()
    pham = spans[("35476", "chamber-house-elect")]
    assert pham.discriminator == "ld-4-position-1"
    assert (pham.start_biennium, pham.end_biennium) == (B, B)
    assert pham.valid_from == date(2026, 11, 3)  # general election day
    assert pham.valid_from_latest == date(2026, 11, 25)  # WSL's first archived listing
    assert pham.valid_to == date(2027, 1, 10)  # the day before 2027-28's term opens
    assert pham.valid_to_earliest == date(2027, 1, 10)  # the statute states it
    assert (pham.start_basis, pham.end_basis) == (BASIS_DATED, BASIS_DATED)
    assert pham.is_active is False  # a member-elect is not serving
    assert spans[("31521", "chamber-house-elect")].discriminator == "ld-4-position-2"
    assert spans[("28000", "chamber-senate-elect")].discriminator == "26"


def test_a_senator_whose_seat_was_not_on_the_ballot_is_no_member_elect():
    """WSL lists all 49 senators; only the ballot says who was elected (the holdover)."""
    assert ("18458", "chamber-senate-elect") not in _build()


def test_certification_bounds_the_window_when_it_comes_first():
    elections = [
        {
            "election_date": DAY,
            "fetched_at": "2026-11-27T08:04:00.000000Z",
            "is_official_results": True,
        },
        {
            "election_date": DAY,
            "fetched_at": "2026-11-20T08:04:00.000000Z",
            "is_official_results": False,
        },
    ]
    late = [{**r, "first_seen_at": "2026-12-15T08:00:00.000000Z"} for r in FIRST_SEEN]
    pham = _build(first_seen=late, elections=elections)[("35476", "chamber-house-elect")]
    assert pham.valid_from_latest == date(2026, 11, 27)


def test_a_listing_after_the_term_opens_clamps_the_window_to_its_end():
    late = [{**r, "first_seen_at": "2027-02-01T08:00:00.000000Z"} for r in FIRST_SEEN]
    pham = _build(first_seen=late)[("35476", "chamber-house-elect")]
    assert pham.valid_from_latest == date(2027, 1, 10)


def test_a_winner_wsl_has_not_listed_has_no_member_elect_yet():
    """No WSL id, no person (§ 2.3): PDC and roster keys are pair-only."""
    sponsors = [s for s in SPONSORS if s["member_id"] != "35476"]
    spans = _build(sponsors=sponsors)
    assert not [s for s in spans.values() if s.discriminator == "ld-4-position-1"]


def test_two_members_on_one_won_seat_is_never_guessed():
    sponsors = [*SPONSORS, _sponsor("40000", "House", "Pham", "R", "4")]
    assert not [
        s for s in _build(sponsors=sponsors).values() if s.discriminator == "ld-4-position-1"
    ]


def test_a_tied_race_is_dropped_by_the_ballot_rule():
    results = [r for r in RESULTS if r["race"] != _house(4, 2)] + [
        _result(_house(4, 2), "Rob Chase", REP, 100),
        _result(_house(4, 2), "Rob Tupper", DEM, 100),
    ]
    assert ("31521", "chamber-house-elect") not in _build(sos_results=results)


def test_no_member_elect_before_the_2026_general():
    """§ 2.5: earlier ballots have no first-evidence wires, so a window would be invented."""
    old = [{**s, "biennium": "2025-26"} for s in SPONSORS]
    results = [{**r, "election_date": "20241105"} for r in RESULTS]
    assert _build(sponsors=old, sos_results=results) == {}


def test_a_biennium_whose_seating_general_is_not_staged_builds_nothing():
    assert _build(sos_results=[]) == {}


def test_a_member_elect_with_no_evidence_is_skipped_not_dated_by_a_guess():
    assert _build(first_seen=[], elections=[]) == {}


def test_a_departure_before_the_term_ends_the_member_elect_there():
    """A member-elect who dies or withdraws before the term: the operator's ``departed``."""
    event = SimpleNamespace(
        member_id="35476",
        kind="departed",
        effective_date=date(2026, 12, 20),
        seat_kind=None,
        seat_discriminator=None,
    )
    pham = _build(events=[event])[("35476", "chamber-house-elect")]
    assert pham.valid_to == date(2026, 12, 20)
    assert pham.valid_to_earliest == date(2026, 12, 20)


def test_the_build_counts_what_the_ballot_won_and_what_it_built():
    """Reported by ``registry_coverage``: WSL lists winners for weeks after the general,
    so an unlisted winner is a count, not a gate."""
    counts: Counter = Counter()
    sponsors = [s for s in SPONSORS if s["member_id"] != "31521"]
    build_elect_spans(
        sponsors=sponsors, sos_results=RESULTS, first_seen=FIRST_SEEN, elections=[], counts=counts
    )
    assert counts == Counter({"won": 3, "built": 2, "unlisted": 1})


def test_a_frame_s_numpy_true_still_reads_as_certified():
    elections = [
        {
            "election_date": DAY,
            "fetched_at": "2026-11-20T08:04:00.000000Z",
            "is_official_results": np.bool_(True),
        }
    ]
    late = [{**r, "first_seen_at": "2026-12-15T08:00:00.000000Z"} for r in FIRST_SEEN]
    pham = _build(first_seen=late, elections=elections)[("35476", "chamber-house-elect")]
    assert pham.valid_from_latest == date(2026, 11, 20)
