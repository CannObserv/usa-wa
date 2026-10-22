"""Annotation windows: the roster's month- and year-precision dates, read (#501 D6a).

``ParsedDate.window`` (#360) bounds every clause the roster dates more coarsely than a
day — "Appointed Oct. 1971", "Resigned 1919", "Elected November 1977" — and until #501
nothing read them. This pass turns each into a window on the member's seat span, after
the operator overlay and before the counterpart clip: an **observed** edge becomes
**dated**, its outer bound MOVES to the window's edge and the inner bound is the
window's other edge. Own evidence beats inference — Rasmussen's start, which the clip
used to derive onto McCutcheon's exit, is his own October 1971 instead, and the
overlap disappears (#501 correction 4).

What reads which clause (the projector's priority, ``_start_date``/``_end_date``):

- **a start**: ``roster_pdf.succession.start_clause``, the projector's own choice —
  swearing-in, else appointment, at month or year precision. A day is an operator
  event's to record (the roster backfill proposed them; #226), never a window.
- **an election opens a start at any precision** (owner, #360, 2026-10-17): a sourced
  EARLIEST start, ``[election, min(C(start), valid_to)]`` — certification and the oath
  come later, so it is never an exact edge. It only ever RAISES ``valid_from``: an
  election before the span's own floor says nothing, since the term starts at the
  convening. An election FOLLOWING a departure is the departure's reason (Cunningham,
  "Resigned; Elected to Congress"), never a start.
- **an end**: death, else resignation, at month or year precision.

An edge an operator event already dates **wins**; a date outside the window is counted
as a contradiction and logged, never moved. A ``derived`` edge is left alone. An
appointment a biennium before its span follows ``_opens_after_seating``'s one-biennium
rule; the key is unchanged either way, so no span re-keys here.

Seat spans only, Senate only: no House span starts before 2001, and party follows the
seat's attestation rather than its dates. Pre-1991 rows only — the roster's later rows
are join evidence, and that era's boundaries are operator events.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.boundary_basis import (
    BASIS_DATED,
    BASIS_OBSERVED,
)
from clearinghouse_domain_legislative.span_kinds import KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import TenureSpan, biennium_end_date
from clearinghouse_domain_legislative.terms import biennium_for_date, previous_biennium
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.succession import (
    END_VERBS,
    parse_annotation,
    start_clause,
)

logger = get_logger(__name__)

EDGE_START = "start"
EDGE_END = "end"

_ELECTED = "elected"
_DAY = "day"


@dataclass(frozen=True)
class EdgeWindow:
    """One clause's window on one member's Senate seat edge."""

    member_id: str
    district: int
    edge: str
    window: tuple[date, date]
    election: bool
    clause: str


def _first(clauses, verbs: tuple[str, ...]):
    for verb in verbs:
        for clause in clauses:
            if clause.verb == verb and clause.parsed.window is not None:
                return clause
    return None


def edge_windows(members: dict[RosterRecord, str]) -> list[EdgeWindow]:
    """Every start and end window the resolved records' annotations state."""
    out: list[EdgeWindow] = []
    for record, member in members.items():
        if record.chamber != "senate" or not record.annotation:
            continue
        clauses = parse_annotation(record.annotation)
        # the projector's own choice, so the window dates the span it keyed
        start = start_clause(clauses)
        if start is not None and (start.verb == _ELECTED or start.parsed.precision != _DAY):
            out.append(
                EdgeWindow(
                    member,
                    record.district,
                    EDGE_START,
                    start.parsed.window,
                    start.verb == _ELECTED,
                    start.text,
                )
            )
        end = _first(clauses, END_VERBS)
        if end is not None and end.parsed.precision != _DAY:
            out.append(
                EdgeWindow(member, record.district, EDGE_END, end.parsed.window, False, end.text)
            )
    return out


def _on_seat(span: TenureSpan, window: EdgeWindow) -> bool:
    return (
        span.member_id == window.member_id
        and span.kind == KIND_SENATE
        and span.discriminator == str(window.district)
    )


def _starts_with(span: TenureSpan, window: EdgeWindow) -> bool:
    """The span whose start this window dates: keyed in the window's biennium, or — an
    appointment only — the one after it (``_opens_after_seating``)."""
    biennium = biennium_for_date(window.window[0])
    if span.start_biennium == biennium:
        return True
    return not window.election and previous_biennium(span.start_biennium) == biennium


def _ends_with(span: TenureSpan, window: EdgeWindow) -> bool:
    """The closed span whose extent holds the window."""
    first, last = window.window
    return span.valid_to is not None and span.valid_from <= last and first <= span.valid_to


def _date_start(span: TenureSpan, window: EdgeWindow, counts: Counter) -> TenureSpan:
    first, last = window.window
    if span.start_basis == BASIS_DATED:
        agrees = first <= span.valid_from if window.election else first <= span.valid_from <= last
        if not agrees:
            counts["contradicted"] += 1
            logger.info(
                "annotation_window_contradicts_event",
                extra={"member_id": span.member_id, "edge": EDGE_START, "clause": window.clause},
            )
        return span
    if span.start_basis != BASIS_OBSERVED:
        counts["not_observed"] += 1
        return span
    end = span.valid_to or date.max
    if window.election:
        if first <= span.valid_from:
            counts["election_not_later"] += 1
            return span
        last = min(biennium_end_date(span.start_biennium), end)
    else:
        last = min(last, end)
    if first > end:
        counts["contradicted"] += 1
        return span
    counts[EDGE_START] += 1
    return replace(
        span,
        valid_from=first,
        start_basis=BASIS_DATED,
        start_window_end=last if last != first else None,
    )


def _date_end(span: TenureSpan, window: EdgeWindow, counts: Counter) -> TenureSpan:
    first, last = window.window
    if span.end_basis == BASIS_DATED:
        if not first <= (span.valid_to or date.max) <= last:
            counts["contradicted"] += 1
            logger.info(
                "annotation_window_contradicts_event",
                extra={"member_id": span.member_id, "edge": EDGE_END, "clause": window.clause},
            )
        return span
    if span.end_basis != BASIS_OBSERVED or span.valid_to is None:
        counts["not_observed"] += 1
        return span
    to = min(last, span.valid_to)
    earliest = max(first, span.valid_from)
    counts[EDGE_END] += 1
    return replace(
        span,
        valid_to=to,
        is_active=False,
        end_basis=BASIS_DATED,
        end_window_start=earliest if earliest != to else None,
    )


def apply_windows(
    families: dict[str, list[TenureSpan]], windows: list[EdgeWindow]
) -> tuple[dict[str, list[TenureSpan]], Counter]:
    """``families`` with every window applied to the one span it dates; order kept.

    A window matching no span, or more than one, is counted and left alone."""
    counts: Counter = Counter()
    out = {source: list(spans) for source, spans in families.items()}
    for window in windows:
        matches = _starts_with if window.edge == EDGE_START else _ends_with
        hits = [
            (source, i)
            for source, spans in out.items()
            for i, span in enumerate(spans)
            if _on_seat(span, window) and matches(span, window)
        ]
        if len(hits) != 1:
            counts["unmatched" if not hits else "ambiguous"] += 1
            continue
        source, i = hits[0]
        dater = _date_start if window.edge == EDGE_START else _date_end
        out[source][i] = dater(out[source][i], window, counts)
    if counts:
        logger.info("annotation_windows_applied", extra={"counts": dict(counts)})
    return out, counts
