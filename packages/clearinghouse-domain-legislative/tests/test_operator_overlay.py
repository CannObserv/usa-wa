"""Operator-succession overlay (#107) — pure, the LD5 Ramos/Hunt golden cases."""

from datetime import date

from clearinghouse_domain_legislative.operator_overlay import (
    SuccessionEvent,
    apply_operator_events,
    latest_event_biennium_by_member,
    log_departure_misses,
    stale_exempt_members,
)
from clearinghouse_domain_legislative.tenure_spans import TenureSpan

CURRENT = "2025-26"


def _span(member, kind, disc, *, start="2025-26", frm=date(2025, 1, 1), to=None, active=True):
    return TenureSpan(
        member_id=member,
        kind=kind,
        discriminator=disc,
        start_biennium=start,
        end_biennium="2025-26",
        valid_from=frm,
        valid_to=to,
        is_active=active,
    )


def _by_key(spans):
    return {(s.member_id, s.kind, s.discriminator): s for s in spans}


def test_departed_closes_all_member_open_spans():
    """Ramos died 2025-04-19 → his Senate seat AND party both close; a bystander is untouched."""
    spans = [
        _span("29091", "chamber-senate", "5"),
        _span("29091", "party", "democratic"),
        _span("00000", "party", "democratic"),  # another member, untouched
    ]
    events = [SuccessionEvent("29091", "departed", date(2025, 4, 19))]

    out = _by_key(
        apply_operator_events(
            spans, events, current_biennium=CURRENT, owned_kinds={"party", "chamber-senate"}
        )
    )
    assert out[("29091", "chamber-senate", "5")].valid_to == date(2025, 4, 19)
    assert out[("29091", "chamber-senate", "5")].is_active is False
    assert out[("29091", "party", "democratic")].valid_to == date(2025, 4, 19)
    assert out[("00000", "party", "democratic")].is_active is True  # bystander untouched


def test_seated_sets_start_on_existing_span():
    """Hunt appointed to Senate 2025-06-03 → her wire-built Senate span starts there."""
    spans = [_span("35410", "chamber-senate", "5")]  # wire built floor→open
    events = [
        SuccessionEvent("35410", "seated", date(2025, 6, 3), "chamber-senate", "5"),
    ]
    out = apply_operator_events(
        spans, events, current_biennium=CURRENT, owned_kinds={"chamber-senate", "party"}
    )
    assert out[0].valid_from == date(2025, 6, 3)
    assert out[0].is_active is True


def test_vacated_closes_named_seat_only():
    """Hunt vacated her House seat 2025-06-03 (chamber move) → House span closes, party open."""
    spans = [
        _span("35410", "chamber-house", "ld-5-position-1"),
    ]
    events = [
        SuccessionEvent("35410", "vacated", date(2025, 6, 3), "chamber-house", "ld-5-position-1"),
    ]
    out = apply_operator_events(
        spans, events, current_biennium=CURRENT, owned_kinds={"chamber-house"}
    )
    assert out[0].valid_to == date(2025, 6, 3)
    assert out[0].is_active is False


def test_seated_synthesizes_when_no_wire_span():
    """An appointee the wire hasn't caught up on yet → the overlay mints their open seat span."""
    events = [SuccessionEvent("99999", "seated", date(2025, 6, 3), "chamber-senate", "5")]
    out = apply_operator_events(
        [], events, current_biennium=CURRENT, owned_kinds={"chamber-senate"}
    )
    assert len(out) == 1
    assert out[0].member_id == "99999"
    assert out[0].kind == "chamber-senate"
    assert out[0].valid_from == date(2025, 6, 3)
    assert out[0].is_active is True
    assert out[0].source_id == "99999:chamber-senate:5:2025-26"


def test_seated_out_of_current_biennium_does_not_synthesize():
    """#119: a historical seated event with no matching span (the daily *restricted* rebuild
    builds only the current cohort) must NOT mint a bogus current-biennium span for a departed
    member. Synthesis is only legitimate for a current-biennium appointee. The unrestricted
    backfill builds the historical span, so this event matches there — no synthesis needed."""
    events = [
        SuccessionEvent("77777", "seated", date(2009, 11, 1), "chamber-house", "ld-16-position-2")
    ]
    out = apply_operator_events([], events, current_biennium=CURRENT, owned_kinds={"chamber-house"})
    assert out == []


def test_seated_in_current_biennium_still_synthesizes():
    """The guard is date-scoped, not blanket: a current-biennium appointee whose wire built no
    span still gets a synthesized open seat (the #107 live case, unchanged)."""
    events = [SuccessionEvent("99999", "seated", date(2026, 6, 3), "chamber-senate", "5")]
    out = apply_operator_events(
        [], events, current_biennium=CURRENT, owned_kinds={"chamber-senate"}
    )
    assert len(out) == 1 and out[0].valid_from == date(2026, 6, 3)


def test_foreign_seat_kind_ignored():
    """A seated event for a seat this builder doesn't own is a no-op (no cross-builder leak)."""
    events = [
        SuccessionEvent("35410", "seated", date(2025, 6, 3), "chamber-house", "ld-5-position-1")
    ]
    out = apply_operator_events(
        [], events, current_biennium=CURRENT, owned_kinds={"chamber-senate", "party"}
    )
    assert out == []


def test_seated_targets_the_covering_tenure_not_a_later_one():
    """Gap-and-return: a stale seated event dates the tenure whose window it falls in, never a
    later same-seat tenure (CR finding 1). Member served 2019-20 (gap) then returned 2025-26."""
    early = _span(
        "35410",
        "chamber-senate",
        "5",
        start="2019-20",
        frm=date(2019, 1, 1),
        to=date(2020, 12, 31),
        active=False,
    )
    late = _span("35410", "chamber-senate", "5", start="2025-26", frm=date(2025, 1, 1))
    events = [SuccessionEvent("35410", "seated", date(2019, 3, 1), "chamber-senate", "5")]

    out = apply_operator_events(
        [early, late], events, current_biennium=CURRENT, owned_kinds={"chamber-senate"}
    )
    got = {s.start_biennium: s for s in out}
    assert got["2019-20"].valid_from == date(2019, 3, 1)  # the covering tenure is dated
    assert got["2025-26"].valid_from == date(2025, 1, 1)  # the later tenure is untouched


def test_vacated_synthesizes_closed_span_for_a_mover():
    """#145: a House→Senate mover excluded from the roster has no built House span, so a
    `vacated` event — gated on the per-biennium mover signal — synthesizes their CLOSED
    [floor→date] House tenure directly, instead of the roster re-inclusion that perturbs the
    #103 elimination and splits the backfiller (the reverted 2013-14 tranche)."""
    events = [
        SuccessionEvent("13546", "vacated", date(2014, 1, 22), "chamber-house", "ld-21-position-2")
    ]
    out = apply_operator_events(
        [],
        events,
        current_biennium=CURRENT,
        owned_kinds={"chamber-house"},
        movers_by_biennium={"2013-14": {"13546"}},
    )
    assert len(out) == 1
    s = out[0]
    assert s.member_id == "13546"
    assert s.kind == "chamber-house" and s.discriminator == "ld-21-position-2"
    assert s.valid_from == date(2013, 1, 1)  # biennium floor
    assert s.valid_to == date(2014, 1, 22)  # the vacate date
    assert s.is_active is False
    assert s.start_biennium == "2013-14"
    assert s.source_id == "13546:chamber-house:ld-21-position-2:2013-14"


def test_vacated_no_synth_for_non_mover():
    """The mover gate is a guard: a `vacated` with no built span and NO mover signal for that
    biennium stays a logged no-op — a typo'd event must never mint a bogus closed span."""
    events = [
        SuccessionEvent("99999", "vacated", date(2014, 1, 22), "chamber-house", "ld-21-position-2")
    ]
    out = apply_operator_events(
        [],
        events,
        current_biennium=CURRENT,
        owned_kinds={"chamber-house"},
        movers_by_biennium={"2013-14": {"13546"}},  # 99999 is not a mover
    )
    assert out == []


def test_vacated_no_synth_without_movers_param():
    """Senate/committee builders pass no movers map → a `vacated` no-match stays a no-op
    (unchanged behavior; synthesis is opt-in via the mover signal)."""
    events = [
        SuccessionEvent("13546", "vacated", date(2014, 1, 22), "chamber-house", "ld-21-position-2")
    ]
    out = apply_operator_events([], events, current_biennium=CURRENT, owned_kinds={"chamber-house"})
    assert out == []


def test_vacated_synth_gate_is_per_biennium():
    """A member who is a mover in a *different* biennium doesn't get a synthesized span for this
    date — the gate keys on the biennium of the event's own effective_date."""
    events = [
        SuccessionEvent("13546", "vacated", date(2014, 1, 22), "chamber-house", "ld-21-position-2")
    ]
    out = apply_operator_events(
        [],
        events,
        current_biennium=CURRENT,
        owned_kinds={"chamber-house"},
        movers_by_biennium={"2011-12": {"13546"}},  # mover in 2011-12, not 2013-14
    )
    assert out == []


def test_vacated_closes_built_span_even_for_a_mover():
    """If a span IS built for the seat (the mover wasn't excluded, or a later-biennium tenure),
    the existing close path wins — synthesis is only the no-built-span fallback."""
    spans = [_span("13546", "chamber-house", "ld-21-position-2")]
    events = [
        SuccessionEvent("13546", "vacated", date(2025, 6, 3), "chamber-house", "ld-21-position-2")
    ]
    out = apply_operator_events(
        spans,
        events,
        current_biennium=CURRENT,
        owned_kinds={"chamber-house"},
        movers_by_biennium={"2025-26": {"13546"}},
    )
    assert len(out) == 1 and out[0].valid_to == date(2025, 6, 3)  # closed, not a second synth


def test_a_movers_seating_dates_the_house_span_its_vacated_synthesizes(caplog):
    """usa-wa#461 (member 15814): seated ld-18-position-1 2011-01-05, vacated 2012-06-25 on
    moving to the Senate. The wire builds no House span (mover exclusion), so `vacated`
    synthesizes it — but events apply in date order, so the seating ran first, matched
    nothing and left the synthesized span on its 2011-01-01 floor."""
    events = [
        SuccessionEvent("15814", "seated", date(2011, 1, 5), "chamber-house", "ld-18-position-1"),
        SuccessionEvent("15814", "vacated", date(2012, 6, 25), "chamber-house", "ld-18-position-1"),
    ]
    with caplog.at_level("INFO"):
        (out,) = apply_operator_events(
            [],
            events,
            current_biennium=CURRENT,
            owned_kinds={"chamber-house"},
            movers_by_biennium={"2011-12": {"15814"}},
        )
    assert out.source_id == "15814:chamber-house:ld-18-position-1:2011-12"
    assert (out.valid_from, out.valid_to) == (date(2011, 1, 5), date(2012, 6, 25))
    assert out.is_active is False
    assert "operator_seated_no_span_out_of_biennium" not in caplog.messages


def test_a_movers_prior_biennium_seating_dates_the_synthesized_span():
    """#461 × #282: the synthesized span opens on its biennium's floor, so a mover seated in
    the biennium before — a mid-biennium appointee absent from that roster — dates it through
    the lookback, exactly as for a built span. Its key stays the vacated's biennium."""
    events = [
        SuccessionEvent("15814", "seated", date(2010, 3, 9), "chamber-house", "ld-18-position-1"),
        SuccessionEvent("15814", "vacated", date(2012, 6, 25), "chamber-house", "ld-18-position-1"),
    ]
    (out,) = apply_operator_events(
        [],
        events,
        current_biennium=CURRENT,
        owned_kinds={"chamber-house"},
        movers_by_biennium={"2011-12": {"15814"}},
    )
    assert out.source_id == "15814:chamber-house:ld-18-position-1:2011-12"
    assert (out.valid_from, out.valid_to) == (date(2010, 3, 9), date(2012, 6, 25))


def test_two_vacateds_in_one_biennium_mint_one_movers_span(caplog):
    """Both would key ``…:2013-14``: the earlier's window ends before the later's date, so a
    window check mints twice and publishes two rows under one ``span_key``. The earliest
    vacate ends the tenure; the later one is a logged miss."""
    events = [
        SuccessionEvent("13546", "vacated", date(2014, 1, 22), "chamber-house", "ld-21-position-2"),
        SuccessionEvent("13546", "vacated", date(2014, 3, 1), "chamber-house", "ld-21-position-2"),
    ]
    with caplog.at_level("INFO"):
        (out,) = apply_operator_events(
            [],
            events,
            current_biennium=CURRENT,
            owned_kinds={"chamber-house"},
            movers_by_biennium={"2013-14": {"13546"}},
        )
    assert (out.valid_from, out.valid_to) == (date(2013, 1, 1), date(2014, 1, 22))
    assert "operator_vacated_no_span" in caplog.messages


def test_a_movers_vacated_never_mints_a_built_spans_key():
    """A built span keyed in the vacate's biennium but starting after its date does not hold
    it, so the window check would mint beside it — two rows under one ``span_key``. The key
    is taken; the inverted event stays a logged no-op (`operator_event_predates_span`)."""
    built = _span(
        "13546", "chamber-house", "ld-21-position-2", start="2013-14", frm=date(2014, 2, 1)
    )
    out = apply_operator_events(
        [built],
        [
            SuccessionEvent(
                "13546", "vacated", date(2014, 1, 22), "chamber-house", "ld-21-position-2"
            )
        ],
        current_biennium=CURRENT,
        owned_kinds={"chamber-house"},
        movers_by_biennium={"2013-14": {"13546"}},
    )
    assert out == [built]


def test_latest_event_biennium_by_member():
    """Each member's latest operator-event biennium (by biennium_for_date of the max
    effective_date); a member with events in two biennia resolves to the later one."""
    events = [
        SuccessionEvent("100", "vacated", date(2013, 6, 4), "chamber-house", "ld-28-position-1"),
        SuccessionEvent("100", "seated", date(2019, 2, 1), "chamber-senate", "28"),  # later
        SuccessionEvent("200", "departed", date(2013, 5, 29)),
    ]
    assert latest_event_biennium_by_member(events) == {"100": "2019-20", "200": "2013-14"}


def test_stale_exempt_members_is_biennium_scoped():
    """#145 CR: a member is exempt from the stale exclusion only in biennia <= their latest event
    biennium. O'Ban (event 2013-14) is exempt in 2013-14 and earlier, NOT in 2021-22 — where his
    cumulative-wire ghost (post-2020 election loss) must stay stale-excluded so his Senate span is
    not extended past his real departure."""
    events = [
        SuccessionEvent("17217", "vacated", date(2013, 6, 4), "chamber-house", "ld-28-position-1"),
    ]
    latest = latest_event_biennium_by_member(events)
    assert stale_exempt_members(latest, "2011-12") == {"17217"}  # earlier — exempt
    assert stale_exempt_members(latest, "2013-14") == {"17217"}  # same — exempt
    assert stale_exempt_members(latest, "2015-16") == set()  # later — NOT exempt
    assert stale_exempt_members(latest, "2021-22") == set()  # much later — NOT exempt (the fix)


def test_stale_exempt_members_multiple_events_uses_latest():
    """A member with events in two biennia is exempt through the LATER one (their span is built up
    to their last asserted boundary)."""
    events = [
        SuccessionEvent("100", "seated", date(2013, 7, 3), "chamber-house", "ld-28-position-1"),
        SuccessionEvent("100", "departed", date(2019, 3, 1)),
    ]
    latest = latest_event_biennium_by_member(events)
    assert stale_exempt_members(latest, "2017-18") == {"100"}  # <= 2019-20
    assert stale_exempt_members(latest, "2019-20") == {"100"}
    assert stale_exempt_members(latest, "2021-22") == set()


def test_stale_exempt_members_empty():
    assert stale_exempt_members({}, "2013-14") == set()


def test_departed_does_not_truncate_a_span_the_member_re_enters():
    """usa-wa#267 — the resign-and-return shape, measured live on Elmer C. Huntley.

    He resigned 1965-03-26 and was appointed to Senate LD9 on 1967-04-24. Both facts are
    right; `build_tenure_spans` merges contiguous biennia into ONE party span, so a
    person-scoped `departed` truncated his party tenure at 1965 while his 1967-72 Senate
    span survived — leaving him holding a seat with no party affiliation under it for five
    years. The guard is per-span: close what the member really left, keep what they return
    to.
    """
    party = _span(
        "huntley",
        "party",
        "republican",
        start="1957-58",
        frm=date(1957, 1, 1),
        to=date(1972, 12, 31),
        active=False,
    )
    senate = _span(
        "huntley",
        "chamber-senate",
        "9",
        start="1967-68",
        frm=date(1967, 4, 24),
        to=date(1972, 12, 31),
        active=False,
    )
    events = [SuccessionEvent("huntley", "departed", date(1965, 3, 26))]

    out = _by_key(
        apply_operator_events(
            [party, senate],
            events,
            current_biennium=CURRENT,
            owned_kinds={"party", "chamber-senate"},
        )
    )
    # The party span covers the return, so truncating it would strand the Senate seat.
    assert out[("huntley", "party", "republican")].valid_to == date(1972, 12, 31)
    # The seat he returned to begins after the departure — never in scope to begin with.
    assert out[("huntley", "chamber-senate", "9")].valid_from == date(1967, 4, 24)
    assert out[("huntley", "chamber-senate", "9")].valid_to == date(1972, 12, 31)


def test_departed_still_closes_when_the_member_never_returns():
    """The guard must not blunt the ordinary case: a death closes everything it covers.

    A later span only earns protection when it starts AFTER the event — a seat already
    running at the date is exactly what a death ends (the Ramos shape, #107).
    """
    party = _span(
        "smith",
        "party",
        "democratic",
        start="1933-34",
        frm=date(1933, 1, 1),
        to=date(1946, 12, 31),
        active=False,
    )
    senate = _span(
        "smith",
        "chamber-senate",
        "12",
        start="1933-34",
        frm=date(1933, 1, 1),
        to=date(1946, 12, 31),
        active=False,
    )
    events = [SuccessionEvent("smith", "departed", date(1942, 11, 17))]

    out = _by_key(
        apply_operator_events(
            [party, senate],
            events,
            current_biennium=CURRENT,
            owned_kinds={"party", "chamber-senate"},
        )
    )
    assert out[("smith", "party", "democratic")].valid_to == date(1942, 11, 17)
    assert out[("smith", "chamber-senate", "12")].valid_to == date(1942, 11, 17)


# ---------------------------------------------------------------------------
# usa-wa#267 — the split, as #289 leaves it. #267 made a `departed` close a re-entered span
# at the departure and reopen it at the return, rather than truncating the member's whole
# tenure; #289 then decided that a gap in elected service is no evidence at all about PARTY
# membership, so the two halves are rejoined by `merge_party_continuity` on the way out.
#
# The machinery below is still load-bearing, which is why these tests still drive it: the
# merge can only rejoin spans that EXIST, and without the split the departure truncates the
# party tenure and nothing reopens it. What changed is the assertion — for party the observable
# end state is one span across the gap; for a SEAT it is still two tenures, because a seat
# someone stopped holding is a tenure that ended.


def test_a_re_entered_party_tenure_survives_the_gap_whole():
    """Huntley: resigned 1965-03-26, appointed to Senate LD9 1967-04-24, served to 1972.

    759 days without a seat, and a Republican throughout (#289). The span the
    member re-enters is closed and reopened here and rejoined on the way out —
    what must never happen is the pre-#267 truncation, which left him holding a
    Senate seat with no party span under it for five years."""
    party = _span(
        "huntley",
        "party",
        "republican",
        start="1957-58",
        frm=date(1957, 1, 1),
        to=date(1972, 12, 31),
        active=False,
    )
    senate = _span(
        "huntley",
        "chamber-senate",
        "9",
        start="1967-68",
        frm=date(1967, 4, 24),
        to=date(1972, 12, 31),
        active=False,
    )
    out = apply_operator_events(
        [party, senate],
        [SuccessionEvent("huntley", "departed", date(1965, 3, 26))],
        current_biennium=CURRENT,
        owned_kinds={"party", "chamber-senate"},
    )
    parties = [s for s in out if s.kind == "party"]
    assert len(parties) == 1, "one affiliation, not one per seat tenure"
    [party_span] = parties
    assert (party_span.valid_from, party_span.valid_to) == (date(1957, 1, 1), date(1972, 12, 31))
    # keyed on the EARLIER start, so the surviving source_id is the shipped one
    assert party_span.start_biennium == "1957-58"
    # the SEAT is untouched by the merge — that tenure really did end and restart
    assert [s for s in out if s.kind == "chamber-senate"] == [senate]


def test_a_sitting_members_party_tenure_stays_open_across_the_move():
    """Chapman: a sitting senator who moved House->Senate. The tenure must stay OPEN —
    closing it would retire a serving member's party affiliation, which is what
    power-map's public view showed him as (#289)."""
    party = _span(
        "26176",
        "party",
        "democratic",
        start="2017-18",
        frm=date(2017, 1, 1),
        to=None,
        active=True,
    )
    senate = _span(
        "26176",
        "chamber-senate",
        "24",
        start="2025-26",
        frm=date(2025, 1, 1),
        to=None,
        active=True,
    )
    out = apply_operator_events(
        [party, senate],
        [SuccessionEvent("26176", "departed", date(2024, 12, 5))],
        current_biennium=CURRENT,
        owned_kinds={"party", "chamber-senate"},
    )
    parties = [s for s in out if s.kind == "party"]
    assert len(parties) == 1
    [party_span] = parties
    assert party_span.valid_from == date(2017, 1, 1)
    assert party_span.valid_to is None and party_span.is_active is True


def test_departed_split_sees_a_return_another_builder_owns():
    """Pike: returned to a HOUSE seat, which `usa_wa_facts_seats.house.build` owns — invisible
    to the sponsor builder's own span list (#268's structural limit). ``context_spans`` supplies
    it read-only: it informs the split and never appears in the output.

    Still load-bearing under #289, and this is the test that shows why: without the
    context the departure TRUNCATES her party tenure at 2012-12-07 and no second
    half is ever created, so there is nothing for the merge to rejoin and she
    loses six years of Republican membership."""
    party = _span(
        "17158",
        "party",
        "republican",
        start="2011-12",
        frm=date(2011, 1, 1),
        to=date(2018, 12, 31),
        active=False,
    )
    house = _span(
        "17158",
        "chamber-house",
        "ld-18-position-2",
        start="2013-14",
        frm=date(2013, 1, 1),
        to=date(2018, 12, 31),
        active=False,
    )
    out = apply_operator_events(
        [party],
        [SuccessionEvent("17158", "departed", date(2012, 12, 7))],
        current_biennium=CURRENT,
        owned_kinds={"party"},
        context_spans=[house],
    )
    assert all(s.kind == "party" for s in out), "context spans must not be emitted"
    [party_span] = sorted(out, key=lambda s: s.valid_from)
    assert (party_span.valid_from, party_span.valid_to) == (date(2011, 1, 1), date(2018, 12, 31))
    assert party_span.start_biennium == "2011-12"


def test_a_seated_event_dates_the_seat_it_names():
    """Seat-scoped events apply BEFORE person-scoped ones, so a return is read at the precise
    `seated` date rather than a span's biennium floor.

    #289 removed the party-side consequence — the party tenure is one span across
    the gap either way — so what this pins now is the seat: Huntley's Senate
    tenure opens the day he was sworn in, not on the biennium floor the wire
    built it at."""
    party = _span(
        "huntley",
        "party",
        "republican",
        start="1957-58",
        frm=date(1957, 1, 1),
        to=date(1972, 12, 31),
        active=False,
    )
    senate = _span(  # wire-built at the biennium floor; the seated event dates it
        "huntley",
        "chamber-senate",
        "9",
        start="1967-68",
        frm=date(1967, 1, 1),
        to=date(1972, 12, 31),
        active=False,
    )
    events = [  # departed first in the list — the phase split, not the input order, decides
        SuccessionEvent("huntley", "departed", date(1965, 3, 26)),
        SuccessionEvent("huntley", "seated", date(1967, 4, 24), "chamber-senate", "9"),
    ]
    out = apply_operator_events(
        [party, senate],
        events,
        current_biennium=CURRENT,
        owned_kinds={"party", "chamber-senate"},
    )
    [seat] = [s for s in out if s.kind == "chamber-senate"]
    assert seat.valid_from == date(1967, 4, 24)
    [party_span] = [s for s in out if s.kind == "party"]
    assert (party_span.valid_from, party_span.valid_to) == (date(1957, 1, 1), date(1972, 12, 31))


def test_departed_does_not_split_within_one_biennium():
    """A return inside the departure's own biennium would key the new span to the SAME
    biennium — a duplicate `source_id`. Leave the span whole and log instead of emitting a
    key collision the emitter would silently upsert over."""
    party = _span(
        "x",
        "party",
        "democratic",
        start="2013-14",
        frm=date(2013, 1, 1),
        to=date(2014, 12, 31),
        active=False,
    )
    senate = _span(
        "x",
        "chamber-senate",
        "5",
        start="2013-14",
        frm=date(2013, 9, 1),
        to=date(2014, 12, 31),
        active=False,
    )
    out = apply_operator_events(
        [party, senate],
        [SuccessionEvent("x", "departed", date(2013, 3, 1))],
        current_biennium=CURRENT,
        owned_kinds={"party", "chamber-senate"},
    )
    parties = [s for s in out if s.kind == "party"]
    assert len(parties) == 1
    assert parties[0].valid_to == date(2014, 12, 31), "left whole, not truncated"


def test_only_the_earliest_seating_dates_a_tenure():
    """usa-wa#267 — a member is seated ONCE per tenure, so a second `seated` matching the same
    span must not move its start again.

    Christine Rolfes was appointed to Senate LD23 on 2011-07-26 and resigned 2023-08-15. The
    #226 backfill resolved a *second* `seated` (2023-08-23 — her successor's) onto her seat, and
    with seat-scoped events applying before person-scoped ones the later seating overwrote the
    earlier: her twelve-year tenure re-dated to 2023-08-23, and the `departed` then no longer
    matched it at all. The old event order hid this by accident — the `departed` closed the
    window before the spurious seating could match it — which is protection, not a rule.
    """
    span = _span(
        "11998",
        "chamber-senate",
        "23",
        start="2011-12",
        frm=date(2011, 1, 1),
        to=date(2024, 12, 31),
        active=False,
    )
    events = [
        SuccessionEvent("11998", "seated", date(2011, 7, 26), "chamber-senate", "23"),
        SuccessionEvent("11998", "departed", date(2023, 8, 15)),
        SuccessionEvent("11998", "seated", date(2023, 8, 23), "chamber-senate", "23"),
    ]
    out = [
        s
        for s in apply_operator_events(
            [span],
            events,
            current_biennium=CURRENT,
            owned_kinds={"party", "chamber-senate"},
        )
        if s.kind == "chamber-senate"
    ]
    assert len(out) == 1
    assert out[0].valid_from == date(2011, 7, 26), "the first seating dates the tenure"
    assert out[0].valid_to == date(2023, 8, 15), "and the departure still closes it"


def test_a_second_seating_still_dates_a_separate_tenure():
    """The rule is per-SPAN, not per-seat: a gap-and-return member holds the same seat twice,
    and each tenure takes its own seating. Collapsing to one seating per seat would leave the
    second tenure sitting at its biennium floor."""
    first = _span(
        "x",
        "chamber-senate",
        "5",
        start="2011-12",
        frm=date(2011, 1, 1),
        to=date(2012, 12, 31),
        active=False,
    )
    second = _span(
        "x",
        "chamber-senate",
        "5",
        start="2017-18",
        frm=date(2017, 1, 1),
        to=date(2018, 12, 31),
        active=False,
    )
    events = [
        SuccessionEvent("x", "seated", date(2011, 3, 4), "chamber-senate", "5"),
        SuccessionEvent("x", "seated", date(2017, 5, 6), "chamber-senate", "5"),
    ]
    out = sorted(
        apply_operator_events(
            [first, second], events, current_biennium=CURRENT, owned_kinds={"chamber-senate"}
        ),
        key=lambda s: s.valid_from,
    )
    assert [s.valid_from for s in out] == [date(2011, 3, 4), date(2017, 5, 6)]


def test_a_seating_does_not_redate_a_tenure_it_did_not_start():
    """usa-wa#272 — a seating dates the tenure it STARTS, not any tenure it falls inside.

    `_matches_seat` attaches a seat-scoped event to any span whose window contains the date,
    and `build_tenure_spans` merges contiguous biennia — so a thirteen-year tenure's window
    contains almost any date in it. A `seated` belonging to a successor (member 630's
    2016-02-29, against a House tenure keyed 2003-04) landed on the incumbent's span and
    re-dated its start, recording a thirteen-year tenure as six weeks in 2016.
    """
    span = _span(
        "630",
        "chamber-house",
        "ld-44-position-1",
        start="2003-04",
        frm=date(2003, 1, 1),
        to=date(2016, 12, 31),
        active=False,
    )
    out = apply_operator_events(
        [span],
        [SuccessionEvent("630", "seated", date(2016, 2, 29), "chamber-house", "ld-44-position-1")],
        current_biennium=CURRENT,
        owned_kinds={"chamber-house"},
    )
    kept = [s for s in out if s.start_biennium == "2003-04"]
    assert len(kept) == 1
    assert kept[0].valid_from == date(2003, 1, 1), "the 2003 tenure keeps its own start"


def test_a_seating_one_biennium_early_still_dates_its_tenure():
    """The rule must not bite the case it exists to serve. A mid-biennium appointee is absent
    from the sponsor roster of the biennium they were appointed into, so their first span opens
    at the FOLLOWING one — Graham Hunt, appointed 2014-01-17, has only `ld-2-position-1:2015-16`.
    That span starting too late IS the defect the overlay corrects, so "at or after the event's
    biennium" is the bound, not equality (the asymmetry `POSITION_LOOKBACK_YEARS` encodes on the
    resolve side).
    """
    span = _span(
        "hunt",
        "chamber-house",
        "ld-2-position-1",
        start="2015-16",
        frm=date(2014, 1, 1),
        to=date(2016, 12, 31),
        active=False,
    )
    out = apply_operator_events(
        [span],
        [SuccessionEvent("hunt", "seated", date(2014, 1, 17), "chamber-house", "ld-2-position-1")],
        current_biennium=CURRENT,
        owned_kinds={"chamber-house"},
    )
    assert out[0].valid_from == date(2014, 1, 17)


def test_a_same_day_departure_does_not_close_the_seating_it_follows():
    """usa-wa#363, the Derek Stanford case. The roster attests both halves of a
    chamber move on one date — `Resigned July 1, 2019` on the House row, `Appointed
    July 1, 2019 to serve unexpired term` on the Senate row — and the backfill
    projects the resignation as a person-scoped `departed`. Seat-scoped events run
    first, so the seating opens the Senate span and the sweep immediately closes it
    at the same instant: `_close` yields `valid_to = max(d, d)`, a zero-length span,
    and 18 months of a sitting senator's tenure leaves the published record.

    A departure cannot end a tenure that began at the same instant. The member did
    not leave; they moved.
    """
    spans = [
        _span("15809", "chamber-house", "1-position-1", start="2011-12", frm=date(2011, 1, 1)),
        _span("15809", "chamber-senate", "1", start="2019-20", frm=date(2019, 1, 1)),
    ]
    events = [
        SuccessionEvent("15809", "seated", date(2019, 7, 1), "chamber-senate", "1"),
        SuccessionEvent("15809", "departed", date(2019, 7, 1)),
    ]
    out = _by_key(
        apply_operator_events(
            spans,
            events,
            current_biennium=CURRENT,
            owned_kinds={"chamber-senate", "chamber-house"},
        )
    )
    senate = out[("15809", "chamber-senate", "1")]
    assert senate.valid_from == date(2019, 7, 1)
    assert senate.valid_to is None, "the seat he moved INTO must not close on arrival"
    assert senate.is_active is True
    # the seat he moved OUT of still closes — that is what the departure states
    assert out[("15809", "chamber-house", "1-position-1")].valid_to == date(2019, 7, 1)


def test_a_later_departure_still_closes_a_seated_span():
    """The exemption is same-instant only. A member seated in June and gone in
    August genuinely departed the seat, and that tenure must close."""
    spans = [_span("15809", "chamber-senate", "1", start="2025-26")]
    events = [
        SuccessionEvent("15809", "seated", date(2025, 6, 3), "chamber-senate", "1"),
        SuccessionEvent("15809", "departed", date(2025, 8, 15)),
    ]
    (senate,) = apply_operator_events(
        spans, events, current_biennium=CURRENT, owned_kinds={"chamber-senate"}
    )
    assert senate.valid_to == date(2025, 8, 15)
    assert senate.is_active is False


def test_a_same_day_departure_spares_a_synthesized_seating():
    """A current-biennium appointee the wire built no span for is synthesized by
    the seating. It opens at the event date, so the same-instant sweep would close
    it too — and a synthesized span is exactly the one with no wire row to rebuild
    it next run."""
    events = [
        SuccessionEvent("35410", "seated", date(2025, 6, 3), "chamber-senate", "5"),
        SuccessionEvent("35410", "departed", date(2025, 6, 3)),
    ]
    (senate,) = apply_operator_events(
        [], events, current_biennium=CURRENT, owned_kinds={"chamber-senate"}
    )
    assert senate.valid_from == date(2025, 6, 3)
    assert senate.valid_to is None
    assert senate.is_active is True


def test_a_seating_in_the_prior_biennium_dates_the_span_that_opens_after_it():
    """usa-wa#282, the Saldaña shape. Appointed to Senate LD37 on 2016-12-12, in the tail of
    2015-16; the wire first lists her in 2017-18, so her span opens on that biennium's floor.
    The date is before the span's window, so `_matches_seat` finds nothing, and the event was
    recorded, provenanced and inert. The span keeps its key: only `valid_from` moves."""
    span = _span("27290", "chamber-senate", "37", start="2017-18", frm=date(2017, 1, 1))
    (out,) = apply_operator_events(
        [span],
        [SuccessionEvent("27290", "seated", date(2016, 12, 12), "chamber-senate", "37")],
        current_biennium=CURRENT,
        owned_kinds={"chamber-senate"},
    )
    assert out.valid_from == date(2016, 12, 12)
    assert out.source_id == "27290:chamber-senate:37:2017-18", "the span keeps its key"


def test_a_seating_early_in_the_prior_biennium_dates_the_following_span_too():
    """The lookback is a biennium, not a month window. Graham Hunt, appointed 2014-01-18,
    has only `ld-2-position-1:2015-16` opening on its floor: the same absence from the
    appointment biennium's roster, eleven months earlier than the December cases."""
    span = _span(
        "18517",
        "chamber-house",
        "ld-2-position-1",
        start="2015-16",
        frm=date(2015, 1, 1),
        to=date(2016, 2, 2),
        active=False,
    )
    (out,) = apply_operator_events(
        [span],
        [SuccessionEvent("18517", "seated", date(2014, 1, 18), "chamber-house", "ld-2-position-1")],
        current_biennium=CURRENT,
        owned_kinds={"chamber-house"},
    )
    assert out.valid_from == date(2014, 1, 18)


def test_a_seating_two_bienniums_early_dates_nothing():
    """One biennium is the whole reach. A seating four years before the span opens is not
    the appointment that started it — there is a biennium between them the member was not
    listed in at all."""
    span = _span("x", "chamber-senate", "37", start="2017-18", frm=date(2017, 1, 1))
    (out,) = apply_operator_events(
        [span],
        [SuccessionEvent("x", "seated", date(2014, 12, 12), "chamber-senate", "37")],
        current_biennium=CURRENT,
        owned_kinds={"chamber-senate"},
    )
    assert out.valid_from == date(2017, 1, 1)


def test_a_prior_biennium_seating_leaves_a_stated_start_alone():
    """The lookback corrects a QUANTIZED start — a span opening on its biennium floor because
    the builder derived it there. A span whose start some source already dated begins when
    that source says; a seating months before it did not start it."""
    span = _span("x", "chamber-senate", "37", start="2017-18", frm=date(2017, 3, 1))
    (out,) = apply_operator_events(
        [span],
        [SuccessionEvent("x", "seated", date(2016, 12, 12), "chamber-senate", "37")],
        current_biennium=CURRENT,
        owned_kinds={"chamber-senate"},
    )
    assert out.valid_from == date(2017, 3, 1)


def test_a_covering_span_outranks_the_lookback():
    """A seating inside a span's window dates THAT span; the lookback is only for a seating
    no window holds. Gap-and-return in one seat, split at the return: the 2016 seating starts
    the 2015-16 tenure, never the one that reopens in 2017."""
    first = _span(
        "x",
        "chamber-senate",
        "37",
        start="2015-16",
        frm=date(2015, 1, 1),
        to=date(2016, 12, 31),
        active=False,
    )
    second = _span("x", "chamber-senate", "37", start="2017-18", frm=date(2017, 1, 1))
    out = {
        s.start_biennium: s
        for s in apply_operator_events(
            [first, second],
            [SuccessionEvent("x", "seated", date(2016, 3, 1), "chamber-senate", "37")],
            current_biennium=CURRENT,
            owned_kinds={"chamber-senate"},
        )
    }
    assert out["2015-16"].valid_from == date(2016, 3, 1)
    assert out["2017-18"].valid_from == date(2017, 1, 1)


def test_a_lookback_seating_counts_as_the_tenures_one_seating():
    """A tenure dated by the lookback is seated, like any other (#267): a second seating
    that its new window now covers is a re-seating, not a later start."""
    span = _span("27290", "chamber-senate", "37", start="2017-18", frm=date(2017, 1, 1))
    (out,) = apply_operator_events(
        [span],
        [
            SuccessionEvent("27290", "seated", date(2016, 12, 12), "chamber-senate", "37"),
            SuccessionEvent("27290", "seated", date(2017, 1, 9), "chamber-senate", "37"),
        ],
        current_biennium=CURRENT,
        owned_kinds={"chamber-senate"},
    )
    assert out.valid_from == date(2016, 12, 12)


def test_a_lookback_seating_is_not_reported_as_inverted(caplog):
    """`operator_event_predates_span` flags an event dated before the span it names — an
    inverted date the overlay would otherwise skip silently. A seating the lookback applies
    is not that: predating the span is the shape it corrects, so warning on it would raise
    the alarm on every correct application, each run."""
    span = _span("27290", "chamber-senate", "37", start="2017-18", frm=date(2017, 1, 1))
    with caplog.at_level("WARNING"):
        apply_operator_events(
            [span],
            [SuccessionEvent("27290", "seated", date(2016, 12, 12), "chamber-senate", "37")],
            current_biennium=CURRENT,
            owned_kinds={"chamber-senate"},
        )
    assert "operator_event_predates_span" not in caplog.messages


def _departure_lines(caplog, level="INFO"):
    return [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("operator_departed") and r.levelname == level
    ]


def test_a_departure_dated_at_its_tenures_end_is_already_closed(caplog):
    """usa-wa#466: a term-end departure the wire already closed at the biennium end
    (21520, departed 2025-01-04, last span end 2024-12-31) is redundant, not a miss."""
    spans = [_span("21520", "party", "democratic", start="2023-24", to=date(2024, 12, 31))]
    events = [SuccessionEvent("21520", "departed", date(2025, 1, 4))]
    with caplog.at_level("INFO"):
        apply_operator_events(spans, events, current_biennium=CURRENT, owned_kinds={"party"})
    assert _departure_lines(caplog) == ["operator_departed_already_closed"]


def test_a_departure_on_the_span_end_itself_is_already_closed(caplog):
    """`_is_open_through` needs ``valid_to > date``, so a span ending ON the date is
    not swept — and nothing is left for the event to do."""
    spans = [_span("12082", "party", "republican", start="2013-14", to=date(2014, 12, 31))]
    events = [SuccessionEvent("12082", "departed", date(2014, 12, 31))]
    with caplog.at_level("INFO"):
        apply_operator_events(spans, events, current_biennium=CURRENT, owned_kinds={"party"})
    assert _departure_lines(caplog) == ["operator_departed_already_closed"]


def test_a_departure_long_after_the_last_span_end_is_still_a_miss(caplog):
    """Member 656's shape: departed 1996-05-13, last span end 1994-12-31. Sixteen
    months is not a term end — a missing tenure or a misdated event — so it stays
    the overlay's miss signal."""
    spans = [_span("656", "party", "democratic", start="1993-94", to=date(1994, 12, 31))]
    events = [SuccessionEvent("656", "departed", date(1996, 5, 13))]
    with caplog.at_level("INFO"):
        apply_operator_events(spans, events, current_biennium=CURRENT, owned_kinds={"party"})
    assert _departure_lines(caplog) == ["operator_departed_no_open_span"]


def test_a_departure_before_a_later_tenure_is_still_a_miss(caplog):
    """Member 321's shape: a span closed just before the date, but another runs past
    it. The member is not "already closed" — something after the date says they
    came back — so the event is still worth a look."""
    spans = [
        _span("321", "committee", "500", start="1995-96", to=date(1996, 12, 31)),
        _span(
            "321", "committee", "501", start="1999-00", frm=date(1999, 1, 1), to=date(2012, 12, 31)
        ),
    ]
    events = [SuccessionEvent("321", "departed", date(1997, 1, 15))]
    with caplog.at_level("INFO"):
        apply_operator_events(spans, events, current_biennium=CURRENT, owned_kinds={"committee"})
    assert _departure_lines(caplog) == ["operator_departed_no_open_span"]


def test_a_member_with_no_span_is_a_miss_not_already_closed(caplog):
    """A typo'd id holds nothing; there is no tenure for the event to have ended."""
    events = [SuccessionEvent("99999", "departed", date(2025, 1, 4))]
    with caplog.at_level("INFO"):
        apply_operator_events([], events, current_biennium=CURRENT, owned_kinds={"party"})
    assert _departure_lines(caplog) == ["operator_departed_no_open_span"]


def test_a_caller_collecting_applied_departures_takes_over_the_miss(caplog):
    """usa-wa#466: a family runs one overlay per builder, and a senator's departure
    closes nothing in the House one. Passing ``applied_departures`` hands the
    report to the caller: the overlay records what it applied and logs its own
    miss at DEBUG only."""
    senate = [_span("29091", "chamber-senate", "5")]
    departure = SuccessionEvent("29091", "departed", date(2025, 4, 19))
    applied: set[SuccessionEvent] = set()
    with caplog.at_level("DEBUG"):
        apply_operator_events(
            [],
            [departure],
            current_biennium=CURRENT,
            owned_kinds={"chamber-house"},
            applied_departures=applied,
        )
        assert applied == set()
        apply_operator_events(
            senate,
            [departure],
            current_biennium=CURRENT,
            owned_kinds={"chamber-senate"},
            applied_departures=applied,
        )
    assert applied == {departure}
    assert _departure_lines(caplog) == []
    assert _departure_lines(caplog, "DEBUG") == ["operator_departed_no_open_span"]


def test_a_spared_same_instant_seating_counts_as_applied():
    """A chamber move (#363) is a departure the overlay acted on — it closed the seat
    moved out of and spared the one moved into — so it is no miss for the family."""
    spans = [_span("27181", "chamber-senate", "1", frm=date(2019, 1, 1))]
    events = [
        SuccessionEvent("27181", "seated", date(2019, 7, 1), "chamber-senate", "1"),
        SuccessionEvent("27181", "departed", date(2019, 7, 1)),
    ]
    applied: set[SuccessionEvent] = set()
    apply_operator_events(
        spans,
        events,
        current_biennium="2019-20",
        owned_kinds={"chamber-senate"},
        applied_departures=applied,
    )
    assert applied == {events[1]}


def test_log_departure_misses_reports_each_unapplied_departure_once(caplog):
    """The family-level report: a departure no overlay applied is classified once
    against the family's spans; an applied one, and every other kind, says nothing."""
    applied_event = SuccessionEvent("1", "departed", date(2025, 4, 19))
    term_end = SuccessionEvent("2", "departed", date(2025, 1, 4))
    missed = SuccessionEvent("3", "departed", date(2025, 4, 19))
    seated = SuccessionEvent("4", "seated", date(2025, 4, 19), "chamber-senate", "5")
    spans = [_span("2", "party", "democratic", start="2023-24", to=date(2024, 12, 31))]
    with caplog.at_level("INFO"):
        log_departure_misses(
            [applied_event, term_end, missed, seated], applied={applied_event}, spans=spans
        )
    assert [(r.getMessage(), r.member_id) for r in caplog.records] == [
        ("operator_departed_already_closed", "2"),
        ("operator_departed_no_open_span", "3"),
    ]
