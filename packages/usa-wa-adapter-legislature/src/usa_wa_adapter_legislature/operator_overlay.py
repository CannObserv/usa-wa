"""Operator-succession overlay (#107) — pure span-boundary correction.

The authoritative layer that runs **after** ``build_tenure_spans`` and **before**
``emit_spans`` in each span builder, applying the operator's :class:`OperatorEvent`
facts as precise sub-biennium boundaries the wire can't supply:

- ``departed`` (person-scoped) — close **every** open span of the member at the date.
- ``vacated`` (seat-scoped) — close the member's **one** named seat span at the date.
- ``seated`` (seat-scoped) — open the member's named seat span at the date (adjust the
  built span's ``valid_from``, or **synthesize** the span if the wire built none).

Each builder passes ``owned_kinds`` — the span ``kind``\\s it produces — so an event for a
seat another builder owns is ignored here (a ``seated chamber-house`` event is the SOS House
builder's, not the sponsor builder's). ``departed`` only ever touches the spans in this
builder's set, so the three builders together close a dead member's seat + party + committees.

Pure and idempotent: the daily refresh re-drives every builder, so the overlay re-applies on
each run and the wire can never win back a corrected span. A member with an operator event
must be **exempted from the #105 hygiene exclusions** upstream (see
:func:`event_member_ids`) so their span is actually built for the overlay to date.
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
from usa_wa_adapter_legislature.tenure_spans import TenureSpan

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


def event_member_ids(events: Iterable[SuccessionEvent]) -> set[str]:
    """The member ids named by ``events`` — the set a builder exempts from its #105 hygiene
    exclusions so an operator-touched member's span is built for the overlay to date."""
    return {e.member_id for e in events}


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


def apply_operator_events(
    spans: list[TenureSpan],
    events: Iterable[SuccessionEvent],
    *,
    current_biennium: str,
    owned_kinds: Iterable[str],
) -> list[TenureSpan]:
    """Return ``spans`` with the operator events applied (a new list; inputs untouched).

    ``owned_kinds`` scopes the seat-scoped events to the kinds this builder produces — a
    seated/vacated for a foreign seat kind is ignored (another builder owns it). ``departed``
    closes every open span already present in ``spans`` (all this builder's owned kinds)."""
    owned = set(owned_kinds)
    result = list(spans)
    for event in events:
        if event.kind == KIND_DEPARTED:
            for i, span in enumerate(result):
                if span.member_id == event.member_id and _is_open_through(
                    span, event.effective_date
                ):
                    result[i] = _close(span, event.effective_date)
        elif event.kind == KIND_VACATED:
            if event.seat_kind not in owned:
                continue
            hit = False
            for i, span in enumerate(result):
                if _matches_seat(span, event):
                    result[i] = _close(span, event.effective_date)
                    hit = True
            if not hit:
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
                    result[i] = replace(span, valid_from=event.effective_date)
                    hit = True
            if not hit:
                result.append(_synthesize(event, current_biennium))
    return result


def _is_open_through(span: TenureSpan, effective_date: date) -> bool:
    """A span the ``departed`` sweep should close: it began on/before the date and is still
    open (or overstated past it)."""
    return span.valid_from <= effective_date and (
        span.valid_to is None or span.valid_to > effective_date
    )
