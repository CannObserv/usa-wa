"""Committee succession attestation store (usa-wa#124 C2).

The write side of the operator-attested lineage layer: persist a
:class:`CommitteeSuccessionEvent` (provenance + projection), idempotent on its
deterministic natural key, with append-only supersede-for-corrections — the same
convention as the #107 operator-events store, and sharing its ``usa_wa_operator``
provenance :class:`Source`.

Every write appends a hashed ``FetchEvent`` + ``RawPayload`` (the serialized event, so
the integrity sweep covers operator committee-lineage facts, #54). A correction appends a
new row and stamps the prior one's ``superseded_by_id``; provenance is never mutated.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.committee_succession import (
    OPERATOR_SOURCE_SLUG,
    CommitteeSuccessionEvent,
)

# Re-export the shared operator Source getter so callers need one import.
from usa_wa_adapter_legislature.operator_events_store import (  # noqa: F401
    get_or_create_operator_source,
)


class _InheritYear:
    """Sentinel for :func:`supersede_event`: the caller omitted ``effective_year``, so
    inherit ``prior``'s. Distinct from an explicit ``None``, which *clears* the year."""


#: Pass to :func:`supersede_event` (the default) to inherit ``prior``'s year; pass an
#: explicit ``None`` to clear it, or an ``int`` to set it.
INHERIT_YEAR = _InheritYear()


def succession_source_id(
    subject_source_id: str, linked_source_id: str, slug: str, effective_year: int | None
) -> str:
    """Deterministic natural key: ``{slug}:{subject}:{linked}[:{year}]``.

    A re-ingest of the same attestation is idempotent; a corrected ``effective_year`` is a
    *distinct* event (so it supersedes rather than silently overwriting)."""
    parts = [slug, subject_source_id, linked_source_id]
    if effective_year is not None:
        parts.append(str(effective_year))
    return ":".join(parts)


def _serialize_event(
    *,
    subject_source_id: str,
    linked_source_id: str,
    slug: str,
    effective_year: int | None,
    evidence_url: str,
    notes: str | None,
) -> bytes:
    """Canonical JSON bytes for the event — the hashed, archived provenance body."""
    return json.dumps(
        {
            "subject_source_id": subject_source_id,
            "linked_source_id": linked_source_id,
            "slug": slug,
            "effective_year": effective_year,
            "evidence_url": evidence_url,
            "notes": notes,
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


async def record_succession_event(
    session: AsyncSession,
    source: Source,
    *,
    subject_source_id: str,
    linked_source_id: str,
    slug: str,
    effective_year: int | None = None,
    evidence_url: str,
    notes: str | None = None,
    entered_by: str | None = None,
) -> CommitteeSuccessionEvent:
    """Persist a succession attestation (provenance + projection). Idempotent on the
    natural key.

    Returns the projection row. A byte-identical re-ingest neither duplicates the
    FetchEvent/RawPayload nor changes the row; a changed evidence_url/notes updates the
    row and appends fresh provenance (a new content_hash)."""
    sid = succession_source_id(subject_source_id, linked_source_id, slug, effective_year)
    body = _serialize_event(
        subject_source_id=subject_source_id,
        linked_source_id=linked_source_id,
        slug=slug,
        effective_year=effective_year,
        evidence_url=evidence_url,
        notes=notes,
    )
    content_hash = hashlib.sha256(body).digest()

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
            select(CommitteeSuccessionEvent).where(
                CommitteeSuccessionEvent.source == OPERATOR_SOURCE_SLUG,
                CommitteeSuccessionEvent.source_id == sid,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.evidence_url = evidence_url
        existing.notes = notes
        if entered_by is not None:
            existing.entered_by = entered_by
        await session.flush()
        return existing
    row = CommitteeSuccessionEvent(
        source=OPERATOR_SOURCE_SLUG,
        source_id=sid,
        subject_source_id=subject_source_id,
        linked_source_id=linked_source_id,
        slug=slug,
        effective_year=effective_year,
        evidence_url=evidence_url,
        notes=notes,
        entered_by=entered_by,
    )
    session.add(row)
    await session.flush()
    return row


async def supersede_event(
    session: AsyncSession,
    source: Source,
    prior: CommitteeSuccessionEvent,
    *,
    subject_source_id: str | None = None,
    linked_source_id: str | None = None,
    effective_year: int | None | _InheritYear = INHERIT_YEAR,
    evidence_url: str,
    notes: str | None = None,
    entered_by: str | None = None,
) -> CommitteeSuccessionEvent:
    """Record a correction of ``prior`` and stamp ``prior.superseded_by_id``.

    A **re-link** correction (new ``linked_source_id`` — the wrong-successor case) or a
    changed ``effective_year`` mints a *distinct* natural key, so the producer emits it as
    create-new + retract-old (power-map#322). A correction that resolves to ``prior``'s own
    key (same subject/linked/year, only evidence/notes changed) is a plain idempotent
    update and is *not* self-superseded. Unspecified subject/linked default to ``prior``'s;
    ``effective_year`` defaults to :data:`INHERIT_YEAR` (keep ``prior``'s) — pass an
    explicit ``None`` to **clear** the year, or an ``int`` to set it."""
    year = prior.effective_year if isinstance(effective_year, _InheritYear) else effective_year
    corrected = await record_succession_event(
        session,
        source,
        subject_source_id=subject_source_id or prior.subject_source_id,
        linked_source_id=linked_source_id or prior.linked_source_id,
        slug=prior.slug,
        effective_year=year,
        evidence_url=evidence_url,
        notes=notes,
        entered_by=entered_by,
    )
    if corrected.id != prior.id:
        prior.superseded_by_id = corrected.id
        await session.flush()
    return corrected


async def current_events(session: AsyncSession) -> Sequence[CommitteeSuccessionEvent]:
    """Every non-superseded succession attestation — the producer's input set."""
    return (
        (
            await session.execute(
                select(CommitteeSuccessionEvent).where(
                    CommitteeSuccessionEvent.source == OPERATOR_SOURCE_SLUG,
                    CommitteeSuccessionEvent.superseded_by_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )


async def superseded_events(session: AsyncSession) -> Sequence[CommitteeSuccessionEvent]:
    """Every superseded succession attestation — the producer's retract candidate set (#127).

    A corrected/re-linked attestation leaves its prior row superseded; the producer
    retracts the corresponding PM event unless an active attestation still asserts the same
    ``(subject, slug, linked)`` identity (a year-only correction keeps the identity)."""
    return (
        (
            await session.execute(
                select(CommitteeSuccessionEvent).where(
                    CommitteeSuccessionEvent.source == OPERATOR_SOURCE_SLUG,
                    CommitteeSuccessionEvent.superseded_by_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
