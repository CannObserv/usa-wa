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

**"Dated" is the edge's basis** (usa-wa#501 D4/D6), never its geometry. Until #501
this module compared ``valid_from`` with the span's own biennium floor, which read
Woodall's real ``seated 1957-01-01`` as quantized (pair #3 of the occupancy
residue). Every writer now states its basis, so a stated edge is a ``dated`` one,
a quantized edge an ``observed`` one, and an edge this pass moves becomes
``derived`` — which is also what stops a clipped boundary being read back as
evidence by the next pair.

Pure, idempotent, order- and length-preserving: it runs over the **union** of
every span family, because a seat's two holders routinely come from different
builders (a WSL-joined incumbent and a minted pre-1991 successor), and a clip
scoped to one family is blind across exactly the seam the handoff crosses.

**The member axis (usa-wa#282).** One member holds one seat at a time, so the
same rule applies to a member's two seats: a dated seating into the new seat
closes the old one's quantized ceiling. The overlay's prior-biennium lookback is
what made it reachable — Janéa Holmquist, sworn into Senate LD13 on 2006-12-06,
kept her House span's 2006-12-31 ceiling and held two seats for 25 days::

    3430  seat:house:ld-13:position-1  2003-01-01 → 2006-12-31   ← runs to the ceiling
    3430  seat:senate:ld-13            2006-12-06 → 2014-12-31   dated seating

An *open* old seat is the live cohort, which a ``vacated`` dates (#145), so it is
never clipped from geometry. A declined member overlap is left for
``assignments_one_seat_per_member`` to report; ``unclipped`` is the seat axis's.

**Capacity (usa-wa#362, #501 D4).** A seat can lawfully hold more than one
principal — the 1889 Senate seated five in LD-19 under one ``role_key``. With more
than one lawful holder, geometry cannot say which pair is a succession, so a pair
whose overlap touches a period of capacity above one is declined as
``multi_holder_period`` and left for the occupancy gate to judge by count. The
capacity is passed in (:class:`CapacityPeriod`, keyed by seat) because deriving it
is a jurisdiction's business; a seat absent from it holds one, which is the
behaviour every caller had before.

**Membership (#501 D5).** Only principals are seat counterparts. A substitute
serving under a principal on leave is not that principal's successor, so the seat
axis never pairs either with it; the member axis is unchanged.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from clearinghouse_domain_legislative.boundary_basis import (
    BASIS_DATED,
    BASIS_DERIVED,
    BASIS_OBSERVED,
)
from clearinghouse_domain_legislative.membership_types import MEMBERSHIP_PRINCIPAL
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

#: The clip would leave a span with no duration at all. Reachable only when two
#: holders share a boundary date exactly; a zero-length tenure is never a fact
#: about the world, so the overlap stands instead.
UNCLIPPED_DEGENERATE = "degenerate"

#: The overlap touches a period in which the seat lawfully holds more than one
#: principal (#501 D4): not a succession, so not this module's to resolve.
UNCLIPPED_MULTI_HOLDER_PERIOD = "multi_holder_period"

#: Sentinel for an open span in an ordering comparison — an unbounded end sorts
#: last and outlives every closed counterpart.
_OPEN = date(9999, 12, 31)


@dataclass(frozen=True)
class CapacityPeriod:
    """How many principals a seat holds at once over ``[valid_from, valid_to]``.

    ``valid_to`` is the period's last day, **inclusive**, and ``None`` runs through the
    present; ``max_holders`` is ``None`` for no limit. A tenure's ``valid_to`` is a
    handoff date instead — the published ``role_periods`` dataset states both
    conventions (#501 D1)."""

    valid_from: date
    valid_to: date | None
    max_holders: int | None


#: Seat ``(kind, discriminator)`` → its capacity periods, as a caller derives them.
Capacity = Mapping[tuple[str, str], Sequence[CapacityPeriod]]


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


def _overlaps(a: TenureSpan, b: TenureSpan) -> bool:
    """The gate's own predicate, including its touch exclusion: two tenures that
    share a boundary (``a.valid_to == b.valid_from``) are a handoff, not an overlap."""
    if b.valid_to is not None and a.valid_from > b.valid_to:
        return False
    if a.valid_to is not None and b.valid_from > a.valid_to:
        return False
    return a.valid_to != b.valid_from and b.valid_to != a.valid_from


def _stated_start(span: TenureSpan) -> bool:
    """The span's ``valid_from`` is a date some source stated: its basis is ``dated``.

    Not ``derived``, which is what stops a cascade. Clipping Charnley's LD-44 start
    onto North's dated 1979-12-31 exit moved it off his biennium floor; read back as
    evidence, it then "dated" Bradburn — who opened on that very day — and closed his
    tenure at its own opening, a zero-length span (usa-wa#360 follow-up). A derived
    boundary is an inference, not a fact, and its basis now says so.
    """
    return span.start_basis == BASIS_DATED


def _stated_exit(span: TenureSpan) -> date | None:
    """The span's stated end, or ``None`` when it has none: open, observed (the
    builder's ceiling) or derived (another tenure's boundary)."""
    return span.valid_to if span.end_basis == BASIS_DATED else None


def _tenure_order(span: TenureSpan) -> tuple[date, date]:
    """Sort key placing the earlier tenure first — start, then end, with an open
    end sorting last. The predecessor is the one this ranks lower."""
    return (span.valid_from, span.valid_to or _OPEN)


def _in_biennium(day: date, biennium: str) -> bool:
    """``day`` falls in ``biennium``'s two calendar years — the bound a clipped
    ``valid_from`` must respect, since ``source_id`` is keyed on the biennium."""
    start, end = parse_biennium(biennium)
    return start <= day.year <= end


def _multi_holder(capacity: Capacity, a: TenureSpan, b: TenureSpan) -> bool:
    """The overlap of ``a`` and ``b`` touches a period in which their seat holds more
    than one principal (or has no limit)."""
    periods = capacity.get((a.kind, a.discriminator), ())
    start = max(a.valid_from, b.valid_from)
    end = min(a.valid_to or _OPEN, b.valid_to or _OPEN)
    return any(
        (p.max_holders is None or p.max_holders > 1)
        and p.valid_from <= end
        and start <= (p.valid_to or _OPEN)
        for p in periods
    )


def clip_seat_counterparts(
    spans: Iterable[TenureSpan], *, capacity: Capacity | None = None
) -> SeatClipResult:
    """Apply the rule to every single-holder seat in ``spans``.

    The returned ``spans`` are positionally identical to the input — same length,
    same order — so a caller that unioned several families can re-split the result
    by slicing (:func:`clip_seat_families`). ``capacity`` names the seats that hold
    more than one principal, and when; a seat it omits holds one.
    """
    work = list(spans)
    unclipped: list[UnclippedOverlap] = []
    seat_capacity: Capacity = capacity or {}

    # The member axis runs FIRST (#282). Run second, the seat pass has already
    # clipped a mover's old ceiling onto their successor's later seating and
    # marked it derived, so the move could no longer close it: the member holds
    # both seats between the two dates. First, no seat-derived start exists yet
    # to pose as a stated seating, and the seat pass works on — and reports the
    # residue of — the geometry the member axis leaves.
    members: dict[str, list[int]] = defaultdict(list)
    for i, span in enumerate(work):
        if span.kind in SINGLE_HOLDER_KINDS:
            members[span.member_id].append(i)
    for _member, positions in sorted(members.items()):
        ordered = sorted(
            positions, key=lambda p: (*_tenure_order(work[p]), work[p].kind, work[p].discriminator)
        )
        for outer, i in enumerate(ordered):
            for j in ordered[outer + 1 :]:
                _resolve_move(work, i, j)

    # Principals only (#501 D5): a substitute is nobody's counterpart.
    seats: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, span in enumerate(work):
        if span.kind in SINGLE_HOLDER_KINDS and span.membership_type == MEMBERSHIP_PRINCIPAL:
            seats[(span.kind, span.discriminator)].append(i)

    for (kind, discriminator), positions in sorted(seats.items()):
        # Oldest handoff first, so a clip is visible to the next pair on the seat:
        # a three-holder seat resolves first→second before second→third.
        ordered = sorted(positions, key=lambda p: (*_tenure_order(work[p]), work[p].member_id))
        for outer, i in enumerate(ordered):
            for j in ordered[outer + 1 :]:
                reason = _resolve_pair(work, i, j, seat_capacity)
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


def _quantized_exit(span: TenureSpan) -> bool:
    """The span closes where the builder observed it last — its basis is
    ``observed`` — not open, not a stated date, and not an edge already moved."""
    return span.end_basis == BASIS_OBSERVED


def _resolve_move(work: list[TenureSpan], i: int, j: int) -> None:
    """Close the member's old seat at a dated seating into a new one (usa-wa#282).

    ``i`` ranks before ``j`` in tenure order, so ``work[i]`` is the seat the member
    left. Every refusal leaves the overlap standing for the member gate."""
    old, new = work[i], work[j]
    if (old.kind, old.discriminator) == (new.kind, new.discriminator):
        return  # one seat held twice — a merged tenure (#267), not a move
    if not _overlaps(old, new):
        return
    if not (_quantized_exit(old) and _stated_start(new)):
        return
    boundary = new.valid_from
    if boundary <= old.valid_from:
        return  # the old seat would have no duration at all
    if (old.valid_to or _OPEN) > (new.valid_to or _OPEN):
        return  # the old row outlives the new seat: two tenures merged (#267)
    work[i] = replace(old, valid_to=boundary, is_active=False, end_basis=BASIS_DERIVED)


def _resolve_pair(work: list[TenureSpan], i: int, j: int, capacity: Capacity) -> str | None:
    """Clip one overlapping pair in place. Returns the refusal reason, or ``None``
    when the pair needed no action or was resolved."""
    a, b = work[i], work[j]
    if a.member_id == b.member_id:
        # One person holding a seat across a dormancy gap. Two tenures, one
        # holder — the gate keys on distinct entities and skips these too.
        return None
    if not _overlaps(a, b):
        return None
    if _multi_holder(capacity, a, b):
        return UNCLIPPED_MULTI_HOLDER_PERIOD

    # Predecessor = the tenure that STARTS first; ties break on the earlier end,
    # which is what makes the Houghton/Crow shape (one shared biennium floor, two
    # different exits) resolve to the dated exit rather than to the other row.
    # Ordering on the END instead reads a returning incumbent as the successor of
    # the interlude that displaced him, and then finds neither side dated.
    pred_pos, succ_pos = (i, j) if _tenure_order(a) <= _tenure_order(b) else (j, i)
    pred, succ = work[pred_pos], work[succ_pos]

    # Bound as an Optional rather than a bool so the date narrows on the branch:
    # an open span has no exit to clip to, and a quantized one states no date.
    pred_exit = _stated_exit(pred)
    succ_dated_start = _stated_start(succ)

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
        if succ.valid_to is not None and boundary >= succ.valid_to:
            # `>=`, not `>`: at equality the clip leaves the successor no duration
            # at all. Same geometry as the branch below, reached from the other side:
            # the successor is nested inside the predecessor. Counting it under
            # its own name would split one shape across two rows of the residue
            # taxonomy the gate comment and PIPELINE.md both publish.
            return UNCLIPPED_PREDECESSOR_OUTLIVES
        work[succ_pos] = replace(succ, valid_from=boundary, start_basis=BASIS_DERIVED)
        return None

    boundary = succ.valid_from
    if (pred.valid_to or _OPEN) > (succ.valid_to or _OPEN):
        # usa-wa#267: `build_tenure_spans` merges contiguous biennia, so a
        # predecessor that outlives the successor is one row covering two
        # tenures. Closing it at the handoff would discard the second.
        return UNCLIPPED_PREDECESSOR_OUTLIVES
    if boundary <= pred.valid_from:
        # `_tenure_order` rules out `<` — the predecessor starts no later than the
        # successor — but NOT `==`, and equality is the live case: two holders
        # opening on the same day. Closing the predecessor there would give it no
        # duration. CR 130 removed a `<`-only guard as unreachable, which was true
        # and beside the point; the boundary condition is where the hazard was.
        return UNCLIPPED_DEGENERATE
    work[pred_pos] = replace(pred, valid_to=boundary, is_active=False, end_basis=BASIS_DERIVED)
    return None


def clip_seat_families(
    spans_by_source: dict[str, list[TenureSpan]],
    *,
    capacity: Capacity | None = None,
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
    result = clip_seat_counterparts(flat, capacity=capacity)
    clipped: Sequence[TenureSpan] = result.spans
    out: dict[str, list[TenureSpan]] = {}
    cursor = 0
    for source in sources:
        size = len(spans_by_source[source])
        out[source] = list(clipped[cursor : cursor + size])
        cursor += size
    return out, result.unclipped
