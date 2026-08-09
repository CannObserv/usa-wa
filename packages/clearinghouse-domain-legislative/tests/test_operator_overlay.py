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
