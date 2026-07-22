"""Operator-succession event store (#107) — provisioning + provenance write + read.

The write side of the operator-attestation facility. An operator states a succession
fact (``departed`` / ``seated`` on a date); :func:`record_operator_event` persists it as
a first-class provenance fact under the ``usa_wa_operator`` :class:`Source`:

1. Serialize the event to canonical JSON and hash it (``sha256`` — so the #54 integrity
   sweep covers operator facts identically to a live wire).
2. Append a :class:`FetchEvent` + :class:`RawPayload` (the JSON body), byte-identical
   re-ingests deduped (append-only, no pile-up).
3. Upsert the queryable :class:`OperatorEvent` projection row by its natural key.

A **correction** that moves the effective date is a *new* event (distinct natural key)
that :func:`supersede_event` stamps onto the prior row's ``superseded_by_id``. Provenance
is never mutated (#54). :func:`current_events` returns only non-superseded rows — what the
overlay consumes on every build.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import (
    FetchEvent,
    FetchStatus,
    RawPayload,
    RetentionPolicy,
    Source,
)
from clearinghouse_domain_legislative.identity import Assignment
from clearinghouse_domain_legislative.operator_events import (
    KIND_DEPARTED,
    KIND_SEATED,
    OPERATOR_SOURCE_SLUG,
    OperatorEvent,
    event_source_id,
)
from usa_wa_adapter_legislature.span_emit import (
    ASSIGNMENT_CITATION_TYPE,
    CitationTarget,
    add_field_citation,
)
from usa_wa_adapter_legislature.tenure_spans import TenureSpan


async def get_or_create_operator_source(
    session: AsyncSession, jurisdiction: Jurisdiction
) -> Source:
    """Get-or-create the ``usa_wa_operator`` :class:`Source` (idempotent, archival)."""
    existing = (
        await session.execute(select(Source).where(Source.slug == OPERATOR_SOURCE_SLUG))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = Source(
        jurisdiction_id=jurisdiction.id,
        name="WA operator succession attestations",
        slug=OPERATOR_SOURCE_SLUG,
        kind="operator",
        base_url=None,
        reliability=1.0,
        cache_ttl_days=1,
        # Operator attestations are a permanent provenance record, not a re-pullable
        # cache — exempt from any future RawPayload GC (#54).
        retention_policy=RetentionPolicy.archival,
    )
    session.add(row)
    await session.flush()
    return row


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


async def _provenance_recorded(
    session: AsyncSession, source_id, resource_id: str, content_hash: bytes
) -> bool:
    """True if a byte-identical attestation is already on record (append-only dedup)."""
    hit = (
        await session.execute(
            select(FetchEvent.id).where(
                FetchEvent.source_id == source_id,
                FetchEvent.resource_id == resource_id,
                FetchEvent.content_hash == content_hash,
            )
        )
    ).first()
    return hit is not None


async def record_operator_event(
    session: AsyncSession,
    source: Source,
    *,
    member_id: str,
    kind: str,
    reason: str,
    effective_date: date,
    evidence_url: str,
    seat_kind: str | None = None,
    seat_discriminator: str | None = None,
    entered_by: str | None = None,
) -> OperatorEvent:
    """Persist an operator event (provenance + projection). Idempotent on the natural key.

    Returns the projection row. A byte-identical re-ingest neither duplicates the
    FetchEvent/RawPayload nor changes the row; a changed evidence_url/reason updates the
    row and appends fresh provenance (a new content_hash)."""
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
    content_hash = hashlib.sha256(body).digest()

    fetch_event = None
    if not await _provenance_recorded(session, source.id, sid, content_hash):
        fetch_event = FetchEvent(
            source_id=source.id,
            resource_id=sid,
            resource_version_key=content_hash.hex(),
            url=f"urn:usa-wa-operator:{sid}",
            fetched_at=datetime.now(UTC),
            http_status=None,
            content_hash=content_hash,
            status=FetchStatus.ok,
        )
        session.add(fetch_event)
        await session.flush()
        session.add(
            RawPayload(
                fetch_event_id=fetch_event.id,
                content_type="application/json",
                body=body,
                size_bytes=len(body),
            )
        )

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
    source: Source,
    prior: OperatorEvent,
    *,
    reason: str,
    effective_date: date,
    evidence_url: str,
    entered_by: str | None = None,
) -> OperatorEvent:
    """Record a correction of ``prior`` (same member + seat, new date/reason/url) and stamp
    ``prior.superseded_by_id``. A same-date "correction" resolves to ``prior`` itself (a plain
    idempotent update) and is *not* self-superseded."""
    corrected = await record_operator_event(
        session,
        source,
        member_id=prior.member_id,
        kind=prior.kind,
        reason=reason,
        effective_date=effective_date,
        evidence_url=evidence_url,
        seat_kind=prior.seat_kind,
        seat_discriminator=prior.seat_discriminator,
        entered_by=entered_by,
    )
    if corrected.id != prior.id:
        prior.superseded_by_id = corrected.id
        await session.flush()
    return corrected


async def cite_operator_events(
    session: AsyncSession,
    event_rows: Iterable[OperatorEvent],
    spans: Iterable[TenureSpan],
    *,
    owned_kinds: Iterable[str],
    assignment_source: str,
    confidence: float,
) -> int:
    """Attach a **field-level** :class:`Citation` to every Assignment the overlay corrected,
    pointing at the operator attestation — so a corrected boundary traces to *why* (#107/#54).

    The field cited follows the event scope: ``valid_to`` for ``departed``/``vacated`` (an end),
    ``valid_from`` for ``seated`` (a start). Seat-scoped events are filtered by ``owned_kinds``
    (this builder's span kinds). Idempotent across re-drives (``add_field_citation`` dedups).
    Returns the number of citations added."""
    owned = set(owned_kinds)
    span_list = list(spans)
    added = 0
    for row in event_rows:
        # Skip a seat-scoped event for a foreign kind before the target DB query — another
        # builder owns it (CR finding 3: don't query per non-owned event every re-drive).
        if row.kind != KIND_DEPARTED and row.seat_kind not in owned:
            continue
        target = await citation_target_for_event(session, row)
        if target is None:
            continue
        # Cite only the spans whose boundary the event actually set (post-overlay), keyed on
        # the boundary == effective_date — so an already-closed prior-tenure span or a foreign
        # tenure is never spuriously cited (CR findings 1/4).
        field_path = "valid_from" if row.kind == KIND_SEATED else "valid_to"
        if row.kind == KIND_DEPARTED:
            affected = [
                s
                for s in span_list
                if s.member_id == row.member_id and s.valid_to == row.effective_date
            ]
        else:  # seated / vacated — seat-scoped
            affected = [
                s
                for s in span_list
                if s.member_id == row.member_id
                and s.kind == row.seat_kind
                and s.discriminator == row.seat_discriminator
                and getattr(s, field_path) == row.effective_date
            ]
        for span in affected:
            assignment = (
                await session.execute(
                    select(Assignment).where(
                        Assignment.source == assignment_source,
                        Assignment.source_id == span.source_id,
                    )
                )
            ).scalar_one_or_none()
            if assignment is None:
                continue
            if await add_field_citation(
                session,
                entity_type=ASSIGNMENT_CITATION_TYPE,
                entity_id=assignment.id,
                field_path=field_path,
                target=target,
                confidence=confidence,
            ):
                added += 1
    return added


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


async def citation_target_for_event(
    session: AsyncSession, event: OperatorEvent
) -> CitationTarget | None:
    """``(fetch_event_id, fetched_at, resource_id)`` for the latest attestation of ``event`` —
    the span_emit citation target so an overlay-touched Assignment cites the operator fact.
    The event's ``source_id`` is the operator-namespaced FetchEvent ``resource_id``. Scoped to
    the ``usa_wa_operator`` Source so a resource_id collision with another source can't select
    the wrong FetchEvent. None if no provenance is on record (shouldn't happen for a recorded
    event)."""
    operator_source_id = (
        select(Source.id).where(Source.slug == OPERATOR_SOURCE_SLUG).scalar_subquery()
    )
    row = (
        await session.execute(
            select(FetchEvent.id, FetchEvent.fetched_at, FetchEvent.resource_id)
            .where(
                FetchEvent.resource_id == event.source_id,
                FetchEvent.source_id == operator_source_id,
            )
            .order_by(FetchEvent.fetched_at.desc(), FetchEvent.id.desc())
        )
    ).first()
    return (row[0], row[1], row[2]) if row is not None else None


__all__ = [
    "ASSIGNMENT_CITATION_TYPE",
    "KIND_DEPARTED",
    "KIND_SEATED",
    "current_events",
    "citation_target_for_event",
    "get_or_create_operator_source",
    "record_operator_event",
    "supersede_event",
]
