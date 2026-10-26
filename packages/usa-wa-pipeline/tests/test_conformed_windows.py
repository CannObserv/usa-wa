"""Annotation windows (#501 D6a): a month, a year or an election dates an observed edge."""

from dataclasses import replace
from datetime import date

from clearinghouse_domain_legislative.span_kinds import KIND_PARTY, KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_pipeline.conformed.spans import ROSTER_SOURCE, SOURCE
from usa_wa_pipeline.conformed.windows import EdgeWindow, apply_windows, edge_windows


def _record(annotation: str | None, *, year: int = 1971, district: int = 29) -> RosterRecord:
    return RosterRecord(
        district=district,
        chamber="senate",
        year=year,
        order=2,
        name="A. L. Rasmussen",
        party_token="D",
        annotation=annotation,
        page_number=0,
    )


def _span(member="ras:1971", *, start="1971-72", end="1971-72", frm=None, to=None, **kw):
    first = int(start[:4])
    frm = frm or date(first, 1, 1)
    to = to if to is not None else date(int(end[:4]) + 1, 12, 31)
    return TenureSpan(
        member_id=member,
        kind=kw.pop("kind", KIND_SENATE),
        discriminator=kw.pop("disc", "29"),
        start_biennium=start,
        end_biennium=end,
        valid_from=frm,
        valid_to=to,
        is_active=False,
        **kw,
    )


def _windows(annotation: str, **kw) -> list[EdgeWindow]:
    return edge_windows({_record(annotation, **kw): "ras:1971"})


# --- edge_windows: which clauses date which edge -------------------------------


def test_a_month_appointment_dates_the_start() -> None:
    (window,) = _windows("Appointed Oct. 1971")
    assert (window.edge, window.window, window.election) == (
        "start",
        (date(1971, 10, 1), date(1971, 10, 31)),
        False,
    )


def test_an_election_opens_a_start_whatever_its_precision() -> None:
    """Owner, #360, 2026-10-17: an election date is a sourced EARLIEST start."""
    (window,) = _windows("Elected November 4, 1958 to serve unexpired term", year=1957)
    assert (window.edge, window.window[0], window.election) == ("start", date(1958, 11, 4), True)


def test_a_day_dated_appointment_is_an_events_job() -> None:
    """A day is what an operator event records; a window never stands in for one."""
    assert _windows("Appointed Feb. 11, 1974 to serve 1974 Ex. S.") == []


def test_a_coarse_departure_dates_the_end() -> None:
    (window,) = _windows("Resigned July 1900", year=1899)
    assert (window.edge, window.window) == ("end", (date(1900, 7, 1), date(1900, 7, 31)))


def test_a_day_dated_departure_is_an_events_job() -> None:
    assert _windows("Deceased Aug. 9, 1971") == []


def test_a_swearing_in_outranks_an_appointment() -> None:
    (window,) = _windows("Appointed 1935; Sworn in Feb. 1935")
    assert window.window == (date(1935, 2, 1), date(1935, 2, 28))


def test_the_house_and_unannotated_rows_carry_nothing() -> None:
    house = replace(_record("Appointed Oct. 1971"), chamber="house")
    assert edge_windows({house: "m", _record(None): "m"}) == []


# --- apply_windows: which span, and what moves -----------------------------------


def _apply(spans, windows, source=ROSTER_SOURCE):
    families, counters = apply_windows({source: spans}, windows)
    return families[source], counters


def test_an_observed_start_becomes_the_dated_window() -> None:
    """Rasmussen's outer bound MOVES to the window's edge: his McCutcheon overlap
    disappears instead of downgrading to a may (#501 correction 4)."""
    (span,), counters = _apply([_span()], _windows("Appointed Oct. 1971"))
    assert (span.valid_from, span.start_basis, span.valid_from_latest) == (
        date(1971, 10, 1),
        "dated",
        date(1971, 10, 31),
    )
    assert counters["start"] == 1


def test_an_election_window_runs_to_the_biennium_ceiling() -> None:
    (span,), _ = _apply(
        [_span(start="1957-58", end="1957-58")],
        _windows("Elected November 4, 1958 to serve unexpired term", year=1957),
    )
    assert (span.valid_from, span.start_basis, span.valid_from_latest) == (
        date(1958, 11, 4),
        "dated",
        date(1958, 12, 31),
    )


def test_an_election_never_lowers_a_start() -> None:
    """An election before the span's own floor carries nothing: the term starts at the
    convening (#501 correction 5)."""
    windows = _windows("Elected November 1972", year=1971)
    (span,), counters = _apply([_span(start="1973-74", end="1973-74")], windows)
    assert span.start_basis == "observed"
    assert counters["start"] == 0


def test_an_appointment_a_biennium_early_dates_the_following_span() -> None:
    """`_opens_after_seating`'s one-biennium rule: the key is unchanged."""
    windows = _windows("Appointed Dec. 1972", year=1971)
    (span,), _ = _apply([_span(start="1973-74", end="1973-74")], windows)
    assert (span.start_biennium, span.valid_from, span.start_basis) == (
        "1973-74",
        date(1972, 12, 1),
        "dated",
    )


def test_an_event_dated_edge_wins_and_a_contradiction_is_counted() -> None:
    seated = _span(frm=date(1971, 8, 9), start_basis="dated")
    (span,), counters = _apply([seated], _windows("Appointed Oct. 1971"))
    assert span == seated
    assert counters["contradicted"] == 1


def test_an_event_inside_the_window_agrees() -> None:
    seated = _span(frm=date(1971, 10, 4), start_basis="dated")
    (span,), counters = _apply([seated], _windows("Appointed Oct. 1971"))
    assert span == seated
    assert counters["contradicted"] == 0


def test_a_derived_edge_is_left_for_the_clip_to_have_moved() -> None:
    moved = _span(frm=date(1971, 8, 9), start_basis="derived")
    (span,), _ = _apply([moved], _windows("Appointed Oct. 1971"))
    assert span == moved


def test_an_observed_end_becomes_the_dated_window() -> None:
    (span,), counters = _apply(
        [_span(start="1899-00", end="1899-00")], _windows("Resigned July 1900", year=1899)
    )
    assert (span.valid_to, span.end_basis, span.valid_to_earliest) == (
        date(1900, 7, 31),
        "dated",
        date(1900, 7, 1),
    )
    assert counters["end"] == 1


def test_only_the_seat_span_moves() -> None:
    party = _span(kind=KIND_PARTY, disc="democratic")
    (span,), _ = _apply([party], _windows("Appointed Oct. 1971"))
    assert span == party


def test_a_joined_member_is_dated_in_the_wsl_family() -> None:
    windows = edge_windows({_record("Elected Nov. 1973 to serve unexpired term"): "446"})
    (span,), _ = _apply([_span("446", start="1973-74", end="1973-74")], windows, source=SOURCE)
    assert (span.valid_from, span.start_basis) == (date(1973, 11, 1), "dated")


def test_a_window_its_own_biennium_holds_never_reaches_the_next_span() -> None:
    """CR 7 (#517): a split return puts a one-biennium head and its tail side by side
    on one seat. The head's appointment window matches it exactly; the one-biennium
    fallback is for a span keyed AFTER its seating, and must not make the tail a
    second candidate that drops the window as ambiguous."""
    windows = _windows("Appointed Oct. 1971")
    head = _span(start="1971-72", end="1971-72")
    tail = _span(start="1973-74", end="1973-74")
    (dated, untouched), counters = _apply([head, tail], windows)
    assert "ambiguous" not in counters
    assert (dated.valid_from, dated.start_basis) == (date(1971, 10, 1), "dated")
    assert untouched == tail


def test_a_window_matching_no_span_is_counted() -> None:
    _spans, counters = _apply([], _windows("Appointed Oct. 1971"))
    assert counters["unmatched"] == 1


def test_an_election_to_another_office_opens_nothing() -> None:
    """Cunningham: the congressional election is why he left the Senate."""
    assert [
        w.edge for w in _windows("Resigned; Elected to Congress, 7th District May 17, 1977")
    ] == []
