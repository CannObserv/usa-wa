"""Counterpart clipping (#360) — a dated succession moves both sides, or neither.

A seat holds one person at a time. The operator overlay (#226/#107) carries the
roster's dated mid-term boundaries, but it applies each event to **the span the
event names**. The counterpart span on the other side of the handoff keeps its
biennium-derived edge, so the two overlap across the gap between the dated
boundary and the quantized one — 91 pairs when #359's gate first measured it.

Two mirror-image shapes, both artifacts of quantization rather than of the
sources disagreeing::

    seat:senate:ld-4   Houghton  1897-01-01 → 1897-08-25   dated exit
                       Crow      1897-01-01 → 1904-12-31   ← opens on the floor

    seat:senate:ld-48  Thompson  1959-01-01 → 1970-12-31   ← runs to the ceiling
                       Andersen  1967-01-05 → 1972-12-27   dated seating

**The rule.** Where exactly one side of an overlap carries a dated boundary, the
other side's *quantized* edge yields to it: a date the roster states is better
evidence than a biennium the builder derived. Where both sides are dated the
sources genuinely contradict each other, and where neither is there is nothing
to clip to — both are left standing for the gate to report.

**Quantization is measured against the span's own biennium**, never a Jan-1 /
Dec-31 pattern. A span whose ``valid_from`` equals its ``start_biennium``'s floor
was quantized there; one that differs was dated by an event. Pattern-matching
would misread a genuine December 31 resignation as a ceiling and clip a real
boundary away.

Pure, idempotent, order- and length-preserving: it runs over the **union** of
every span family, because a seat's two holders routinely come from different
builders (a WSL-joined incumbent and a minted pre-1991 successor), and a clip
scoped to one family is blind across exactly the seam the handoff crosses.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date

from clearinghouse_domain_legislative.span_kinds import SINGLE_HOLDER_KINDS
from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from clearinghouse_domain_legislative.terms import parse_biennium

#: Why an overlap was left standing. Each is a *reported* residue, never a
#: silent one — the conformed gate lists the rows these correspond to.
UNCLIPPED_BOTH_DATED = "both_dated"
UNCLIPPED_NEITHER_DATED = "neither_dated"
#: One shape, one name, whichever branch reaches it: the successor's tenure sits
#: wholly inside the predecessor's, so the predecessor's row is two tenures
#: merged (usa-wa#267) and either clip would discard one of them.
UNCLIPPED_PREDECESSOR_OUTLIVES = "predecessor_outlives_successor"
UNCLIPPED_START_LEAVES_BIENNIUM = "start_leaves_biennium"

#: Sentinel for an open span in an ordering comparison — an unbounded end sorts
#: last and outlives every closed counterpart.
_OPEN = date(9999, 12, 31)


@dataclass(frozen=True)
class UnclippedOverlap:
    """One overlap the rule declines to resolve, attributed so it stays actionable."""

    kind: str
    discriminator: str
    member_a: str
    member_b: str
    reason: str


@dataclass(frozen=True)
class SeatClipResult:
    """Spans positionally identical to the input, plus every declined overlap."""

    spans: tuple[TenureSpan, ...]
    unclipped: tuple[UnclippedOverlap, ...]


def _quantized_start(span: TenureSpan) -> bool:
    """``valid_from`` sits on the span's own ``start_biennium`` floor — derived, not stated."""
    return span.valid_from == date(parse_biennium(span.start_biennium)[0], 1, 1)


def _quantized_end(span: TenureSpan) -> bool:
    """``valid_to`` sits on the span's own ``end_biennium`` ceiling. An open span is
    neither quantized nor dated — it is unbounded, and handled by the caller."""
    return span.valid_to is not None and span.valid_to == date(
        parse_biennium(span.end_biennium)[1], 12, 31
    )


def _overlaps(a: TenureSpan, b: TenureSpan) -> bool:
    """The gate's own predicate, including its touch exclusion: two tenures that
    share a boundary (``a.valid_to == b.valid_from``) are a handoff, not an overlap."""
    if b.valid_to is not None and a.valid_from > b.valid_to:
        return False
    if a.valid_to is not None and b.valid_from > a.valid_to:
        return False
    return a.valid_to != b.valid_from and b.valid_to != a.valid_from


def _tenure_order(span: TenureSpan) -> tuple[date, date]:
    """Sort key placing the earlier tenure first — start, then end, with an open
    end sorting last. The predecessor is the one this ranks lower."""
    return (span.valid_from, span.valid_to or _OPEN)


def _in_biennium(day: date, biennium: str) -> bool:
    start, end = parse_biennium(biennium)
    return start <= day.year <= end


def clip_seat_counterparts(spans: Iterable[TenureSpan]) -> SeatClipResult:
    """Apply the rule to every single-holder seat in ``spans``.

    The returned ``spans`` are positionally identical to the input — same length,
    same order — so a caller that unioned several families can re-split the result
    by slicing (:func:`clip_seat_families`).
    """
    work = list(spans)
    unclipped: list[UnclippedOverlap] = []
    seats: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, span in enumerate(work):
        if span.kind in SINGLE_HOLDER_KINDS:
            seats[(span.kind, span.discriminator)].append(i)

    for (kind, discriminator), positions in sorted(seats.items()):
        # Oldest handoff first, so a clip is visible to the next pair on the seat:
        # a three-holder seat resolves first→second before second→third.
        ordered = sorted(positions, key=lambda p: (*_tenure_order(work[p]), work[p].member_id))
        for outer, i in enumerate(ordered):
            for j in ordered[outer + 1 :]:
                reason = _resolve_pair(work, i, j)
                if reason is not None:
                    a, b = work[i], work[j]
                    unclipped.append(
                        UnclippedOverlap(
                            kind=kind,
                            discriminator=discriminator,
                            member_a=a.member_id,
                            member_b=b.member_id,
                            reason=reason,
                        )
                    )
    return SeatClipResult(spans=tuple(work), unclipped=tuple(unclipped))


def _resolve_pair(work: list[TenureSpan], i: int, j: int) -> str | None:
    """Clip one overlapping pair in place. Returns the refusal reason, or ``None``
    when the pair needed no action or was resolved."""
    a, b = work[i], work[j]
    if a.member_id == b.member_id:
        # One person holding a seat across a dormancy gap. Two tenures, one
        # holder — the gate keys on distinct entities and skips these too.
        return None
    if not _overlaps(a, b):
        return None

    # Predecessor = the tenure that STARTS first; ties break on the earlier end,
    # which is what makes the Houghton/Crow shape (one shared biennium floor, two
    # different exits) resolve to the dated exit rather than to the other row.
    # Ordering on the END instead reads a returning incumbent as the successor of
    # the interlude that displaced him, and then finds neither side dated.
    pred_pos, succ_pos = (i, j) if _tenure_order(a) <= _tenure_order(b) else (j, i)
    pred, succ = work[pred_pos], work[succ_pos]

    # Bound as an Optional rather than a bool so the date narrows on the branch:
    # an open span has no exit to clip to, and a quantized one states no date.
    pred_exit = pred.valid_to if not _quantized_end(pred) else None
    succ_dated_start = not _quantized_start(succ)

    if (pred_exit is not None) and succ_dated_start:
        return UNCLIPPED_BOTH_DATED
    if pred_exit is None and not succ_dated_start:
        return UNCLIPPED_NEITHER_DATED

    if pred_exit is not None:
        boundary = pred_exit
        if not _in_biennium(boundary, succ.start_biennium):
            # The roster listed the successor biennia before the predecessor's
            # dated exit. That is the sources contradicting each other, not a
            # quantization artifact, and moving the start would put it outside
            # the biennium its own `source_id` is keyed on.
            return UNCLIPPED_START_LEAVES_BIENNIUM
        if succ.valid_to is not None and boundary > succ.valid_to:
            # Same geometry as the branch below, reached from the other side:
            # the successor is nested inside the predecessor. Counting it under
            # its own name would split one shape across two rows of the residue
            # taxonomy the gate comment and PIPELINE.md both publish.
            return UNCLIPPED_PREDECESSOR_OUTLIVES
        work[succ_pos] = replace(succ, valid_from=boundary)
        return None

    boundary = succ.valid_from
    if (pred.valid_to or _OPEN) > (succ.valid_to or _OPEN):
        # usa-wa#267: `build_tenure_spans` merges contiguous biennia, so a
        # predecessor that outlives the successor is one row covering two
        # tenures. Closing it at the handoff would discard the second.
        return UNCLIPPED_PREDECESSOR_OUTLIVES
    # No `boundary < pred.valid_from` guard: `_tenure_order` already ranks the
    # predecessor's start no later than the successor's, and `boundary` IS the
    # successor's start — the inversion it would catch cannot be constructed.
    work[pred_pos] = replace(pred, valid_to=boundary, is_active=False)
    return None


def clip_seat_families(
    spans_by_source: dict[str, list[TenureSpan]],
) -> tuple[dict[str, list[TenureSpan]], tuple[UnclippedOverlap, ...]]:
    """:func:`clip_seat_counterparts` across families, re-split by source.

    The families live in disjoint identity spaces but share the seats, so the
    clip must see the union: an incumbent from the WSL archive and a successor
    minted from the roster PDF hold one Senate seat between them, and either
    family alone sees only half the handoff.

    Returns the declined overlaps alongside the spans so a caller counts them
    without re-running the sweep — the residue is a property of the same pass,
    not of a second one.
    """
    sources: list[str] = list(spans_by_source)
    flat: list[TenureSpan] = [s for source in sources for s in spans_by_source[source]]
    result = clip_seat_counterparts(flat)
    clipped: Sequence[TenureSpan] = result.spans
    out: dict[str, list[TenureSpan]] = {}
    cursor = 0
    for source in sources:
        size = len(spans_by_source[source])
        out[source] = list(clipped[cursor : cursor + size])
        cursor += size
    return out, result.unclipped
