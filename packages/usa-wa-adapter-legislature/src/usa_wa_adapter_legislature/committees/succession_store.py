"""Committee succession attestation store (usa-wa#124 C2).

The write side of the operator-attested lineage layer: persist a
:class:`CommitteeSuccessionEvent` (attestation + projection), idempotent on its
deterministic natural key, with append-only supersede-for-corrections — the same
convention as the #107 operator-events store, and sharing its ``usa_wa_operator`` source.

Every write buffers the serialized event for the raw store
(:mod:`usa_wa_adapter_legislature.operators.raw`), its only provenance since #412 PR F
dropped the Postgres ``FetchEvent`` + ``RawPayload`` copy; the raw-store integrity sweep
covers it (#54). A correction appends a new row and stamps the prior one's
``superseded_by_id``; provenance is never mutated.

:func:`is_registered_committee` is the #445 check that a WSL id names a registered
committee org. It lives here so both entry points that need it — this store's CLI and the
operator CLI's committee seats — import it from the store rather than from each other.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.registry import KIND_ORG, RegistryKey
from clearinghouse_domain_legislative.committee_succession import (
    OPERATOR_SOURCE_SLUG,
    CommitteeSuccessionEvent,
)
from usa_wa_adapter_legislature.operators.raw import PendingAttestations
from usa_wa_common.orgs import STRUCTURAL_ORGS


class _InheritYear:
    """Sentinel for :func:`supersede_event`: the caller omitted ``effective_year``, so
    inherit ``prior``'s. Distinct from an explicit ``None``, which *clears* the year."""


#: Pass to :func:`supersede_event` (the default) to inherit ``prior``'s year; pass an
#: explicit ``None`` to clear it, or an ``int`` to set it.
INHERIT_YEAR = _InheritYear()

#: The namespace of committee org keys — both ends of a link must be registered in it.
_COMMITTEE_SOURCE = "usa_wa_legislature"

#: A WSL committee ``Id`` is an integer — negative for some Other bodies (JLARC is ``-5``).
_WSL_COMMITTEE_ID = re.compile(r"-?\d+")


async def is_registered_committee(session: AsyncSession, source_id: str) -> bool:
    """Whether a WSL ``Id`` is a registered committee org (#445).

    The registrar binds every staged committee id — standing, Joint and Other alike —
    plus the ``STRUCTURAL_ORGS`` ids under one namespace, so a committee is a registered
    key that is not structural. The integer shape backs the denylist: a key never unbinds,
    so a structural org later dropped from ``STRUCTURAL_ORGS`` keeps its key, and only the
    shape still refuses it (CR 2). A merge chain ends at a live survivor, so registered
    means live.
    """
    if source_id in STRUCTURAL_ORGS or not _WSL_COMMITTEE_ID.fullmatch(source_id):
        return False
    key = await session.scalar(
        select(RegistryKey.id).where(
            RegistryKey.kind == KIND_ORG,
            RegistryKey.natural_key == f"{_COMMITTEE_SOURCE}:{source_id}",
        )
    )
    return key is not None


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


async def record_succession_event(
    session: AsyncSession,
    *,
    raw: PendingAttestations,
    subject_source_id: str,
    linked_source_id: str,
    slug: str,
    effective_year: int | None = None,
    evidence_url: str,
    notes: str | None = None,
    entered_by: str | None = None,
) -> CommitteeSuccessionEvent:
    """Persist a succession attestation (attestation + projection). Idempotent on the
    natural key.

    Returns the projection row. ``raw`` buffers the attestation body for the raw store,
    which deduplicates a byte-identical re-ingest against the resource's newest record;
    a changed evidence_url/notes updates the row and archives the new body. Required
    since #412 PR F: the raw store is the attestation's only provenance."""
    sid = succession_source_id(subject_source_id, linked_source_id, slug, effective_year)
    body = _serialize_event(
        subject_source_id=subject_source_id,
        linked_source_id=linked_source_id,
        slug=slug,
        effective_year=effective_year,
        evidence_url=evidence_url,
        notes=notes,
    )
    raw.add(sid, body, datetime.now(UTC))

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
    prior: CommitteeSuccessionEvent,
    *,
    raw: PendingAttestations,
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
        raw=raw,
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
