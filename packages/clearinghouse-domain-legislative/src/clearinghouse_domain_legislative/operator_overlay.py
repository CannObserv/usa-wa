"""Operator-succession overlay (#107) — pure span-boundary correction.

The authoritative layer that runs **after** ``build_tenure_spans`` and **before**
``emit_spans`` in each span builder, applying the operator's :class:`OperatorEvent`
facts as precise sub-biennium boundaries the wire can't supply:

- ``departed`` (person-scoped) — close every open span of the member at the date, **except one
  the member re-enters** (usa-wa#267): ``build_tenure_spans`` merges contiguous biennia into a
  single span, so for a member who left and later returned that one span covers both tenures
  and truncating it discards the second. Skipped-and-logged, never silent.
- ``vacated`` (seat-scoped) — close the member's **one** named seat span at the date.
- ``seated`` (seat-scoped) — open the member's named seat span at the date (adjust the
  built span's ``valid_from``, or **synthesize** the span if the wire built none — but only
  for a *current-biennium* appointee: a seated event dated outside ``current_biennium`` with
  no built span is a historical appointee the daily restricted rebuild doesn't build, and
  synthesizing would mint a bogus current-biennium seat for a departed member (#119); the
  unrestricted backfill builds their span, so the event matches there instead).

Each builder passes ``owned_kinds`` — the span ``kind``\\s it produces — so an event for a
seat another builder owns is ignored here (a ``seated chamber-house`` event is the SOS House
builder's, not the sponsor builder's). ``departed`` only ever touches the spans in this
builder's set, so the three builders together close a dead member's seat + party + committees.

Pure and idempotent: the daily refresh re-drives every builder, so the overlay re-applies on
each run and the wire can never win back a corrected span. A member with an operator event
must be **exempted from the #105 stale exclusion** upstream so their span is built for the
overlay to date — but only through their latest event biennium (see
:func:`stale_exempt_members`); a committee-stale member in a *later* biennium is a genuine
post-event ghost whose span must not be extended (the #145 CR biennium-scoping fix).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.operator_events import (
    KIND_DEPARTED,
    KIND_SEATED,
    KIND_VACATED,
)
from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from clearinghouse_domain_legislative.terms import biennium_for_date, parse_biennium

logger = get_logger(__name__)


@dataclass(frozen=True)
class SuccessionEvent:
    """The overlay's pure input unit — an :class:`OperatorEvent` projected free of ORM/DB.
    ``seat_kind``/``seat_discriminator`` are set for the seat-scoped kinds, None for
    ``departed``."""

    member_id: str
    kind: str
    effective_date: date
    seat_kind: str | None = None
    seat_discriminator: str | None = None


def from_rows(rows: Iterable[object]) -> list[SuccessionEvent]:
    """Project :class:`OperatorEvent` ORM rows (or any object with the same attributes) into
    pure :class:`SuccessionEvent`\\s for the overlay."""
    return [
        SuccessionEvent(
            member_id=r.member_id,  # type: ignore[attr-defined]
            kind=r.kind,  # type: ignore[attr-defined]
            effective_date=r.effective_date,  # type: ignore[attr-defined]
            seat_kind=r.seat_kind,  # type: ignore[attr-defined]
            seat_discriminator=r.seat_discriminator,  # type: ignore[attr-defined]
        )
        for r in rows
    ]


def latest_event_biennium_by_member(events: Iterable[SuccessionEvent]) -> dict[str, str]:
    """Each member's **latest** operator-event biennium (``biennium_for_date`` of their maximum
    ``effective_date``). A member with events in two biennia resolves to the later one — the last
    boundary the operator asserts for them. Build this **once** per span rebuild; the map is
    biennium-invariant, so :func:`stale_exempt_members` takes it (not ``events``) to avoid
    recomputing it per biennium."""
    out: dict[str, str] = {}
    for e in events:
        b = biennium_for_date(e.effective_date)
        if e.member_id not in out or parse_biennium(b)[0] > parse_biennium(out[e.member_id])[0]:
            out[e.member_id] = b
    return out


def stale_exempt_members(latest_by_member: dict[str, str], biennium: str) -> set[str]:
    """The member ids exempt from a builder's #105 **stale** exclusion *in this biennium* — those
    whose latest operator event (per :func:`latest_event_biennium_by_member`) is in ``biennium``
    or later (compared by start year, #145 CR).

    The exemption exists so an operator-touched member's span is *built* for the overlay to date.
    But it must be **biennium-scoped**: a member who is committee-stale in a biennium AFTER their
    last event is a genuine post-event ghost (e.g. O'Ban, a 2013-14 chamber-mover who lost his
    Senate seat to Nobles in 2020) — exempting them there lets their cumulative-wire ghost survive
    and stretches their span past their real departure (a spurious later-biennium duplicate). The
    global exemption (all event members in every biennium) this replaces was the bug. Safe because
    stale exclusion only ever bites a **committee-absent** member — a genuinely-serving
    event-member is committee-present and never in the stale set, so narrowing the exemption only
    affects true ghosts.

    Takes the precomputed ``latest_by_member`` map (not ``events``) so a builder computes it once
    and filters per biennium."""
    floor = parse_biennium(biennium)[0]
    return {m for m, lb in latest_by_member.items() if parse_biennium(lb)[0] >= floor}


def _span_covers(span: TenureSpan, effective_date: date) -> bool:
    """The span's validity window contains ``effective_date`` (open end = unbounded)."""
    return span.valid_from <= effective_date and (
        span.valid_to is None or effective_date <= span.valid_to
    )


def _matches_seat(span: TenureSpan, event: SuccessionEvent) -> bool:
    """Seat identity **and** the event's date falling inside the span's window — so a
    seat-scoped event applies to the *tenure it dates*, not merely any open span in that seat
    (a gap-and-return member has two spans in one seat; only the covering one is the target)."""
    return (
        span.member_id == event.member_id
        and span.kind == event.seat_kind
        and span.discriminator == event.seat_discriminator
        and _span_covers(span, event.effective_date)
    )


def _close(span: TenureSpan, effective_date: date) -> TenureSpan:
    """Close a span at ``effective_date`` (clamped ≥ its own start), marking it inactive."""
    return replace(span, valid_to=max(effective_date, span.valid_from), is_active=False)


def _synthesize(event: SuccessionEvent, current_biennium: str) -> TenureSpan:
    """A seated event whose seat the wire built no span for — mint the open tenure from the
    seat descriptor (keyed on the current biennium, so its ``source_id`` is stable)."""
    return TenureSpan(
        member_id=event.member_id,
        kind=event.seat_kind or "",
        discriminator=event.seat_discriminator or "",
        start_biennium=current_biennium,
        end_biennium=current_biennium,
        valid_from=event.effective_date,
        valid_to=None,
        is_active=True,
    )


def _synthesize_closed(event: SuccessionEvent, biennium: str) -> TenureSpan:
    """A `vacated` event for a **mover** whose House seat the roster deliberately excluded (#105)
    — mint their CLOSED tenure ``[biennium-floor → effective_date]``, keyed on the event's own
    biennium so the ``source_id`` is stable (#145). This replaces the roster re-inclusion that
    perturbed the #103 elimination. Safe where the #119 *open*-synth guard is not: a closed
    historical span cannot inflate the current open-chamber count."""
    start_year, _ = parse_biennium(biennium)
    floor = date(start_year, 1, 1)
    return TenureSpan(
        member_id=event.member_id,
        kind=event.seat_kind or "",
        discriminator=event.seat_discriminator or "",
        start_biennium=biennium,
        end_biennium=biennium,
        valid_from=floor,
        # ``biennium_for_date`` yields a biennium whose floor ≤ the date, so this holds; the
        # ``max`` mirrors ``_close`` and guards ``valid_from ≤ valid_to`` defensively regardless.
        valid_to=max(event.effective_date, floor),
        is_active=False,
    )


def apply_operator_events(
    spans: list[TenureSpan],
    events: Iterable[SuccessionEvent],
    *,
    current_biennium: str,
    owned_kinds: Iterable[str],
    movers_by_biennium: dict[str, set[str]] | None = None,
    context_spans: Iterable[TenureSpan] = (),
) -> list[TenureSpan]:
    """Return ``spans`` with the operator events applied (a new list; inputs untouched).

    ``owned_kinds`` scopes the seat-scoped events to the kinds this builder produces — a
    seated/vacated for a foreign seat kind is ignored (another builder owns it). ``departed``
    closes every open span already present in ``spans`` (all this builder's owned kinds).

    ``context_spans`` (usa-wa#267) are the member's spans that **another builder owns** —
    read-only: they inform the ``departed`` split's search for a return and are never modified,
    closed or returned. Without them the split is blind across the builder seam, which is how
    Liz Pike kept a 2,190-day party gap: her party span is the sponsor builder's and the House
    seat she returned to is ``usa_wa_facts_seats.house.build``'s.

    ``movers_by_biennium`` (#145) maps a biennium to the member ids the #105 mover-exclusion
    dropped from that biennium's House roster. A ``vacated`` event matching no built span
    **synthesizes** the mover's closed House tenure iff the member is a mover *that biennium* —
    the House builder passes this so a chamber-mover's House span is dated without re-including
    them in the roster (which perturbs the #103 elimination). Senate/committee builders omit it."""
    owned = set(owned_kinds)
    movers = movers_by_biennium or {}
    context = list(context_spans)
    result = list(spans)
    # Seat-scoped events apply BEFORE person-scoped ones (usa-wa#267). ``departed`` splits a
    # re-entered span at the member's return, and the return date it reads is a span's
    # ``valid_from`` — which a ``seated`` event may still be about to correct from a biennium
    # floor to the real swearing-in. Ordering by phase rather than by input makes the split
    # read settled starts: Huntley's party tenure reopens 1967-04-24, not 1967-01-01.
    ordered = sorted(events, key=lambda e: (e.kind == KIND_DEPARTED, e.effective_date))
    # A member is seated ONCE per tenure. A second `seated` matching a span it has already
    # dated is a re-seating that belongs to a different tenure, or — as the #226 backfill
    # produced for Christine Rolfes — a successor's seating mis-resolved onto the incumbent.
    # Applying it would re-date a twelve-year tenure to its last day. Per SPAN, not per seat:
    # a gap-and-return member holds one seat twice and each tenure takes its own seating.
    seated_spans: set[tuple[str, str, str, str]] = set()
    for event in ordered:
        _warn_if_predates(result, event)
        if event.kind == KIND_DEPARTED:
            hit = False
            for i, span in enumerate(result):
                if span.member_id == event.member_id and _is_open_through(
                    span, event.effective_date
                ):
                    # A span the member re-enters is the merged row of TWO tenures, so
                    # closing it at the departure discards the second (usa-wa#267: Huntley
                    # resigned 1965 and was appointed to the Senate in 1967, leaving him
                    # holding a seat with no party span under it for five years). Split it:
                    # close the first tenure, reopen at the return.
                    re_entry = _re_entry_after([*result, *context], span, event.effective_date)
                    result[i] = _close(span, event.effective_date)
                    hit = True
                    if re_entry is None:
                        continue
                    tail = _split_tail(span, re_entry.valid_from)
                    if tail is None:
                        # The return lands inside the departure's own biennium, so the tail
                        # would key to the same ``source_id`` and the emitter would upsert one
                        # over the other. Leave the tenure whole rather than emit a collision.
                        result[i] = span
                        logger.info(
                            "operator_departed_split_same_biennium",
                            extra={
                                "member_id": event.member_id,
                                "effective_date": event.effective_date.isoformat(),
                                "span_kind": span.kind,
                                "returns_at": re_entry.valid_from.isoformat(),
                            },
                        )
                        continue
                    result.append(tail)
                    logger.info(
                        "operator_departed_span_split_at_return",
                        extra={
                            "member_id": event.member_id,
                            "effective_date": event.effective_date.isoformat(),
                            "span_kind": span.kind,
                            "span_discriminator": span.discriminator,
                            "returns_at": re_entry.valid_from.isoformat(),
                            "returns_kind": re_entry.kind,
                            "tail_biennium": tail.start_biennium,
                        },
                    )
            if not hit:
                # No open span to close in this builder — a bad member id, an inverted date,
                # or the member is already fully closed here. Never silent (CR finding 10).
                logger.info(
                    "operator_departed_no_open_span",
                    extra={
                        "member_id": event.member_id,
                        "effective_date": event.effective_date.isoformat(),
                    },
                )
        elif event.kind == KIND_VACATED:
            if event.seat_kind not in owned:
                continue
            hit = False
            for i, span in enumerate(result):
                if _matches_seat(span, event):
                    result[i] = _close(span, event.effective_date)
                    hit = True
            if not hit:
                # No built span for the seat. For a #105-excluded chamber-mover (gated on the
                # per-biennium mover signal) synthesize their closed House tenure directly (#145);
                # otherwise it is a typo/inverted event — a logged no-op, never a bogus span.
                biennium = biennium_for_date(event.effective_date)
                if event.member_id in movers.get(biennium, set()):
                    result.append(_synthesize_closed(event, biennium))
                    logger.info(
                        "operator_vacated_synthesized_closed",
                        extra={
                            "member_id": event.member_id,
                            "seat": event.seat_discriminator,
                            "biennium": biennium,
                            "effective_date": event.effective_date.isoformat(),
                        },
                    )
                else:
                    logger.info(
                        "operator_vacated_no_span",
                        extra={"member_id": event.member_id, "seat": event.seat_discriminator},
                    )
        elif event.kind == KIND_SEATED:
            if event.seat_kind not in owned:
                continue
            hit = False
            for i, span in enumerate(result):
                if _matches_seat(span, event):
                    key = (span.member_id, span.kind, span.discriminator, span.start_biennium)
                    if key in seated_spans:
                        logger.info(
                            "operator_seated_tenure_already_dated",
                            extra={
                                "member_id": event.member_id,
                                "seat": event.seat_discriminator,
                                "effective_date": event.effective_date.isoformat(),
                                "span_valid_from": span.valid_from.isoformat(),
                            },
                        )
                        hit = True
                        continue
                    seated_spans.add(key)
                    result[i] = replace(span, valid_from=event.effective_date)
                    hit = True
            if not hit:
                # Synthesis is only ever legitimate for a *current-biennium* appointee the wire
                # hasn't caught up on (#107). A seated event whose date lands outside the current
                # biennium and matches no built span is a **historical** appointee: in the daily
                # restricted rebuild (current cohort only) the wire built no span for them, and
                # synthesizing would mint a bogus current-biennium open seat for a long-departed
                # member (#119) — corrupting the record + tripping the succession invariant. The
                # unrestricted backfill DOES build their span, so this event matches there and
                # never reaches synthesis. Skip + log rather than mint a false seat.
                if _in_biennium(event.effective_date, current_biennium):
                    result.append(_synthesize(event, current_biennium))
                else:
                    logger.info(
                        "operator_seated_no_span_out_of_biennium",
                        extra={
                            "member_id": event.member_id,
                            "seat": event.seat_discriminator,
                            "effective_date": event.effective_date.isoformat(),
                            "current_biennium": current_biennium,
                        },
                    )
    return result


def _in_biennium(effective_date: date, biennium: str) -> bool:
    """The date falls within ``biennium``'s two calendar years (``2025-26`` → 2025 or 2026)."""
    start, end = parse_biennium(biennium)
    return start <= effective_date.year <= end


def _warn_if_predates(spans: list[TenureSpan], event: SuccessionEvent) -> None:
    """Log an inverted-date event (``effective_date`` before a matching span's ``valid_from``).

    Post window-matching the overlay simply *skips* such an event (it matches no covering span),
    so a malformed date would otherwise apply nothing with no signal (CR finding 10). This makes
    the inversion loud rather than silent; the CLI's member/date validation is the primary guard."""
    for span in spans:
        if span.member_id != event.member_id:
            continue
        if event.kind in (KIND_VACATED, KIND_SEATED) and (
            span.kind != event.seat_kind or span.discriminator != event.seat_discriminator
        ):
            continue
        if event.effective_date < span.valid_from:
            logger.warning(
                "operator_event_predates_span",
                extra={
                    "member_id": event.member_id,
                    "kind": event.kind,
                    "effective_date": event.effective_date.isoformat(),
                    "span_valid_from": span.valid_from.isoformat(),
                },
            )
            return


def _split_tail(span: TenureSpan, return_date: date) -> TenureSpan | None:
    """The **second** tenure of a re-entered span: ``[return_date → the original end]``, keyed
    on the return's own biennium so its ``source_id`` is stable across re-drives (the shape
    :attr:`TenureSpan.source_id` already documents — "a post-gap tenure gets a new one").

    ``None`` when the return falls inside the closing span's own start biennium: the tail would
    key to the same ``source_id`` and the emitter would upsert one row over the other, so the
    caller leaves the tenure whole instead. Openness carries over — a member still serving keeps
    an open second tenure, which is what makes the sitting-senator case (Chapman) correct."""
    biennium = biennium_for_date(return_date)
    if biennium == span.start_biennium:
        return None
    return replace(span, start_biennium=biennium, valid_from=return_date)


def _re_entry_after(
    spans: list[TenureSpan], span: TenureSpan, effective_date: date
) -> TenureSpan | None:
    """A span of the same member that **starts after** ``effective_date`` and inside ``span``\'s
    window — the member came back, and ``span`` is the merged row covering both tenures.

    The condition is deliberately narrow (usa-wa#267). A span already running at the date is
    *not* a re-entry: that is precisely what a death or a resignation ends, so the ordinary
    ``departed`` case (Ramos, #107) is untouched. Only a span whose start post-dates the
    departure is evidence the member returned.
    """
    key = (span.kind, span.discriminator, span.start_biennium)
    for other in spans:
        if other.member_id != span.member_id:
            continue
        if (other.kind, other.discriminator, other.start_biennium) == key:
            continue  # itself
        if effective_date < other.valid_from and (
            span.valid_to is None or other.valid_from <= span.valid_to
        ):
            return other
    return None


def _is_open_through(span: TenureSpan, effective_date: date) -> bool:
    """A span the ``departed`` sweep should close: it began on/before the date and is still
    open (or overstated past it)."""
    return span.valid_from <= effective_date and (
        span.valid_to is None or span.valid_to > effective_date
    )
