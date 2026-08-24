"""Operator-succession overlay (#107) — pure, the LD5 Ramos/Hunt golden cases."""

from datetime import date

from clearinghouse_domain_legislative.operator_overlay import (
    SuccessionEvent,
    apply_operator_events,
    latest_event_biennium_by_member,
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
# usa-wa#267 — the split. The guard in #268 kept a re-entered span whole, which is the
# pre-#226 shape and not a regression, but it asserts party membership across a gap the
# member did not serve. Splitting is the faithful model: close at the departure, reopen at
# the return, keyed on the return biennium so the new `source_id` is stable across re-drives.


def test_departed_splits_a_re_entered_span():
    """Huntley: resigned 1965-03-26, appointed to Senate LD9 1967-04-24, served to 1972."""
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
    parties = sorted((s for s in out if s.kind == "party"), key=lambda s: s.valid_from)
    assert len(parties) == 2, "the tenure either side of the gap is two spans, not one"
    first, second = parties
    assert (first.valid_from, first.valid_to) == (date(1957, 1, 1), date(1965, 3, 26))
    assert first.is_active is False
    assert (second.valid_from, second.valid_to) == (date(1967, 4, 24), date(1972, 12, 31))
    # Keyed on the return biennium, so the second tenure has its own stable source_id.
    assert second.start_biennium == "1967-68"
    assert second.source_id != first.source_id


def test_departed_split_reopens_an_open_span():
    """Chapman: a sitting senator who moved House->Senate. The second half must stay OPEN —
    closing it would retire a serving member's party affiliation."""
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
    parties = sorted((s for s in out if s.kind == "party"), key=lambda s: s.valid_from)
    assert len(parties) == 2
    assert parties[0].valid_to == date(2024, 12, 5) and parties[0].is_active is False
    assert parties[1].valid_from == date(2025, 1, 1)
    assert parties[1].valid_to is None and parties[1].is_active is True


def test_departed_split_sees_a_return_another_builder_owns():
    """Pike: returned to a HOUSE seat, which `usa_wa_facts_seats.house.build` owns — invisible
    to the sponsor builder's own span list (#268's structural limit). ``context_spans`` supplies
    it read-only: it informs the split and never appears in the output."""
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
    parties = sorted(out, key=lambda s: s.valid_from)
    assert len(parties) == 2
    assert parties[0].valid_to == date(2012, 12, 7)
    assert (parties[1].valid_from, parties[1].start_biennium) == (date(2013, 1, 1), "2013-14")


def test_departed_split_uses_the_seated_date_not_the_biennium_floor():
    """Seat-scoped events apply BEFORE person-scoped ones, so the return date is the precise
    `seated` date rather than the span's biennium floor. Ordering the other way reopens the
    party tenure months before the member was actually sworn in."""
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
    second = max((s for s in out if s.kind == "party"), key=lambda s: s.valid_from)
    assert second.valid_from == date(1967, 4, 24)


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
