"""Operator-succession overlay (#107) — pure, the LD5 Ramos/Hunt golden cases."""

from datetime import date

from usa_wa_adapter_legislature.operator_overlay import (
    SuccessionEvent,
    apply_operator_events,
    event_member_ids,
)
from usa_wa_adapter_legislature.tenure_spans import TenureSpan

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


def test_foreign_seat_kind_ignored():
    """A seated event for a seat this builder doesn't own is a no-op (no cross-builder leak)."""
    events = [
        SuccessionEvent("35410", "seated", date(2025, 6, 3), "chamber-house", "ld-5-position-1")
    ]
    out = apply_operator_events(
        [], events, current_biennium=CURRENT, owned_kinds={"chamber-senate", "party"}
    )
    assert out == []


def test_event_member_ids():
    events = [
        SuccessionEvent("29091", "departed", date(2025, 4, 19)),
        SuccessionEvent("35410", "seated", date(2025, 6, 3), "chamber-senate", "5"),
    ]
    assert event_member_ids(events) == {"29091", "35410"}
