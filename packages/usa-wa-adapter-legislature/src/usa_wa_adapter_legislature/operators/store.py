"""Operator-succession event store (#107) — the attestation write + read.

The write side of the operator-attestation facility. An operator states a succession
fact (``departed`` / ``seated`` on a date); :func:`record_operator_event` persists it:

1. Serialize the event to canonical JSON and buffer it for the ``usa_wa_operator`` source
   in the raw store (:mod:`.raw`), which the caller flushes once its transaction commits.
   That body, stored under its sha256, is the attestation's provenance: the raw-store
   integrity sweep covers it like any harvested wire (#54). Postgres held a copy too until
   #412 PR F dropped the provenance tables.
2. Upsert the queryable :class:`OperatorEvent` projection row by its natural key.

A **correction** that moves the effective date is a *new* event (distinct natural key)
that :func:`supersede_event` stamps onto the prior row's ``superseded_by_id``. Provenance
is never mutated (#54). :func:`current_events` returns only non-superseded rows — what the
pipeline's overlay consumes on every build.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_domain_legislative.operator_events import (
    ENDING_KINDS,
    OPERATOR_SOURCE_SLUG,
    SEAT_SCOPED_KINDS,
    OperatorEvent,
    event_source_id,
)
from usa_wa_adapter_legislature.operators.raw import PendingAttestations


def _serialize_event(
    *,
    member_id: str,
    kind: str,
    reason: str,
    effective_date: date,
    evidence_url: str,
    seat_kind: str | None,
    seat_discriminator: str | None,
) -> bytes:
    """Canonical JSON bytes for the event — the hashed, archived provenance body."""
    return json.dumps(
        {
            "member_id": member_id,
            "kind": kind,
            "reason": reason,
            "effective_date": effective_date.isoformat(),
            "evidence_url": evidence_url,
            "seat_kind": seat_kind,
            "seat_discriminator": seat_discriminator,
        },
        sort_keys=True,
    ).encode("utf-8")


def _check_seat_scope(kind: str, seat_kind: str | None, seat_discriminator: str | None) -> None:
    """``kind in SEAT_SCOPED_KINDS`` iff both seat parts are present (CR 147).

    Enforced here, at the one place every write path meets — a fresh record or a
    supersede (and the roster backfill, until #412 PR F) — rather than at the CLI
    boundary alone. A
    seat-scoped event with no seat is not malformed on the way in:
    ``event_source_id`` substitutes ``-`` for each missing part, so the row lands
    well-formed and simply matches nothing in any overlay, forever. Half a seat is
    no seat, and a person-scoped kind handed a seat is a different event than the
    caller thinks it is recording.
    """
    seat_scoped = kind in SEAT_SCOPED_KINDS
    has_seat = seat_kind is not None and seat_discriminator is not None
    any_seat = seat_kind is not None or seat_discriminator is not None
    if seat_scoped and not has_seat:
        raise ValueError(
            f"{kind!r} is seat-scoped and needs both seat_kind and seat_discriminator; got "
            f"{seat_kind}:{seat_discriminator} — a seat-less {kind!r} keys on placeholders and "
            "no overlay can ever match it"
        )
    if not seat_scoped and any_seat:
        raise ValueError(
            f"{kind!r} is person-scoped and must not carry a seat; got "
            f"{seat_kind}:{seat_discriminator}"
        )


async def record_operator_event(
    session: AsyncSession,
    *,
    raw: PendingAttestations,
    member_id: str,
    kind: str,
    reason: str,
    effective_date: date,
    evidence_url: str,
    seat_kind: str | None = None,
    seat_discriminator: str | None = None,
    entered_by: str | None = None,
) -> OperatorEvent:
    """Persist an operator event (attestation + projection). Idempotent on the natural key.

    Returns the projection row. ``raw`` buffers the attestation body for the raw store,
    which deduplicates a byte-identical re-ingest against the resource's newest record;
    a changed evidence_url/reason updates the row and archives the new body. Required
    since #412 PR F: the raw store is the attestation's only provenance."""
    _check_seat_scope(kind, seat_kind, seat_discriminator)
    sid = event_source_id(
        member_id,
        kind,
        effective_date,
        seat_kind=seat_kind,
        seat_discriminator=seat_discriminator,
    )
    body = _serialize_event(
        member_id=member_id,
        kind=kind,
        reason=reason,
        effective_date=effective_date,
        evidence_url=evidence_url,
        seat_kind=seat_kind,
        seat_discriminator=seat_discriminator,
    )
    raw.add(sid, body, datetime.now(UTC))

    existing = (
        await session.execute(
            select(OperatorEvent).where(
                OperatorEvent.source == OPERATOR_SOURCE_SLUG, OperatorEvent.source_id == sid
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.reason = reason
        existing.evidence_url = evidence_url
        if entered_by is not None:
            existing.entered_by = entered_by
        await session.flush()
        return existing
    row = OperatorEvent(
        source=OPERATOR_SOURCE_SLUG,
        source_id=sid,
        member_id=member_id,
        kind=kind,
        reason=reason,
        seat_kind=seat_kind,
        seat_discriminator=seat_discriminator,
        effective_date=effective_date,
        evidence_url=evidence_url,
        entered_by=entered_by,
    )
    session.add(row)
    await session.flush()
    return row


async def supersede_event(
    session: AsyncSession,
    prior: OperatorEvent,
    *,
    raw: PendingAttestations,
    reason: str,
    effective_date: date,
    evidence_url: str,
    entered_by: str | None = None,
    kind: str | None = None,
    seat_kind: str | None = None,
    seat_discriminator: str | None = None,
) -> OperatorEvent:
    """Record a correction of ``prior`` (same member, new date/reason/url) and stamp
    ``prior.superseded_by_id``. A same-date "correction" resolves to ``prior`` itself (a plain
    idempotent update) and is *not* self-superseded.

    ``kind`` and the seat default to ``prior``'s, which is the ordinary case: a
    correction restates *when* a boundary was, not *what* it was. Passing them
    **reclassifies** the boundary, which is legal only within
    :data:`ENDING_KINDS` — the whole point being that a projection can change its
    mind about whether a resignation ended a career or only a seat, and provenance
    is append-only (#54), so there is no other way to say so.
    """
    if prior.superseded_by_id is not None:
        # `superseded_by_id` is a chain link (CR 148). Re-stamping it orphans the
        # correction it already points at; a caller here is looking at a retracted
        # row and should be told, not accommodated.
        raise ValueError(
            f"event {prior.id} is already superseded by {prior.superseded_by_id}; correct the "
            "live row, never re-stamp a retracted one"
        )
    new_kind = kind or prior.kind
    if new_kind != prior.kind and not {new_kind, prior.kind} <= ENDING_KINDS:
        raise ValueError(
            f"supersede cannot change kind {prior.kind!r} -> {new_kind!r}: a correction may "
            "restate which ending a boundary was, never turn an ending into a beginning"
        )
    reclassified = new_kind != prior.kind
    if not reclassified:
        passed = (seat_kind, seat_discriminator)
        held = (prior.seat_kind, prior.seat_discriminator)
        if any(v is not None for v in passed) and passed != held:
            # Not silently the prior's (CR 140): a correction restates WHEN a
            # boundary was and, within endings, WHICH ending — never which seat.
            # A caller that disagrees about the seat is describing a different
            # event, and a refusal is the only honest answer to that.
            raise ValueError(
                f"supersede of a {prior.kind!r} keeps its seat {held[0]}:{held[1]}; got "
                f"{passed[0]}:{passed[1]} — a correction never moves a boundary to another seat"
            )
    corrected = await record_operator_event(
        session,
        raw=raw,
        member_id=prior.member_id,
        kind=new_kind,
        reason=reason,
        effective_date=effective_date,
        evidence_url=evidence_url,
        seat_kind=seat_kind if reclassified else prior.seat_kind,
        seat_discriminator=seat_discriminator if reclassified else prior.seat_discriminator,
        entered_by=entered_by,
    )
    if corrected.id != prior.id:
        prior.superseded_by_id = corrected.id
        await session.flush()
    return corrected


async def current_events(
    session: AsyncSession, *, member_ids: Iterable[str] | None = None
) -> Sequence[OperatorEvent]:
    """The current (non-superseded) operator events, optionally scoped to ``member_ids`` —
    what the overlay reads on every build."""
    stmt = select(OperatorEvent).where(OperatorEvent.superseded_by_id.is_(None))
    if member_ids is not None:
        ids = list(member_ids)
        if not ids:
            return []
        stmt = stmt.where(OperatorEvent.member_id.in_(ids))
    return (await session.execute(stmt.order_by(OperatorEvent.effective_date))).scalars().all()


__all__ = [
    "current_events",
    "record_operator_event",
    "supersede_event",
]
