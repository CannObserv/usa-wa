"""Span→Assignment emission (#78 increment 2b-ii, Phase B).

Given merged :class:`~usa_wa_adapter_legislature.tenure_spans.TenureSpan`s (built from the
archived sponsor rosters), resolve each span's WSL :class:`Person` + Role and upsert the
:class:`Assignment` with the span's ``valid_from/valid_to/is_active`` — one merged row per
tenure, replacing the pre-#78 per-biennium rows.

**Provenance — cite every biennium in range (#78 decision), append-only.** A merged span is
attested by *every* biennium's roster it was observed in, so it carries one :class:`Citation`
per covered biennium (a span is a contiguous run, so its biennia = ``bienniums_in_range(start,
end)``), each pointing at that biennium's ``sponsors:<biennium>`` roster :class:`FetchEvent`.
Re-emission is **insert-only** — provenance is write-once for the app role (#54 ``REVOKE
DELETE`` on ``citations``), so a re-run adds only the biennia not yet cited (keyed on the
biennium, not the FetchEvent id, since the daily re-pull mints a fresh FetchEvent). It never
deletes/rewrites, so it converges without piling up **and** runs under the DML app role.

Direct session writes (Phase-B derived rows, like the reconcilers/heal) — no AdapterRunner
fetch. The Assignment source stays ``usa_wa_legislature`` (a legislature-structural fact).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import Citation, FetchEvent
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from usa_wa_adapter_legislature.adapter import SPONSORS_RESOURCE_PREFIX
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors
from usa_wa_adapter_legislature.harvest_committee_meetings import bienniums_in_range
from usa_wa_adapter_legislature.normalize.members import (
    get_or_create_role,
    party_role_source_id,
    resolve_ld_jurisdiction,
    senate_seat_role_source_id,
)
from usa_wa_adapter_legislature.sponsor_observations import KIND_PARTY, KIND_SENATE
from usa_wa_adapter_legislature.tenure_spans import TenureSpan

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"
_ASSIGNMENT_CITATION_TYPE = "assignment"
_MEMBER_ROLE_NAME = "Member"
_MEMBER_ROLE_TYPE = "member"
_SENATE_SEAT_ROLE_NAME = "State Senator"
_SENATE_SEAT_ROLE_TYPE = "state_senator"


async def emit_sponsor_spans(
    session: AsyncSession,
    spans: list[TenureSpan],
    *,
    anchors: BootstrapAnchors,
    reliability: float,
    fetch_events: dict[str, tuple[_ULID, datetime]],
) -> int:
    """Upsert an :class:`Assignment` per span (+ per-biennium citations); return the count.

    ``fetch_events`` maps ``biennium → (fetch_event_id, fetched_at)`` (from the cohort
    provider) for the citations. A span whose Person or Role can't be resolved is logged and
    skipped (never guessed)."""
    emitted = 0
    for span in spans:
        person = await _resolve_person(session, span.member_id)
        if person is None:
            logger.warning("sponsor_span_person_absent", extra={"member_id": span.member_id})
            continue
        role = await _resolve_role(session, span, anchors)
        if role is None:
            logger.info(
                "sponsor_span_role_unresolved",
                extra={"member_id": span.member_id, "kind": span.kind, "disc": span.discriminator},
            )
            continue
        assignment = await _upsert_assignment(session, span, person, role)
        await _ensure_citations(session, assignment, span, reliability, fetch_events)
        emitted += 1
    return emitted


async def _resolve_person(session: AsyncSession, member_id: str) -> Person | None:
    return (
        await session.execute(
            select(Person).where(Person.source == _SOURCE, Person.source_id == member_id)
        )
    ).scalar_one_or_none()


async def _resolve_role(
    session: AsyncSession, span: TenureSpan, anchors: BootstrapAnchors
) -> Role | None:
    """The Role a span binds to: a party ``member`` Role, or a ``(Senate, state_senator, LD)``
    seat Role. Returns ``None`` when the party has no Org anchor or the LD isn't synced."""
    if span.kind == KIND_PARTY:
        slug = span.discriminator
        if slug not in anchors.party_ids:
            return None
        return await get_or_create_role(
            session,
            source_id=party_role_source_id(slug),
            organization_id=anchors.party_ids[slug],
            name=_MEMBER_ROLE_NAME,
            role_type=_MEMBER_ROLE_TYPE,
        )
    if span.kind == KIND_SENATE:
        ld = int(span.discriminator)
        jurisdiction = await resolve_ld_jurisdiction(session, ld)
        if jurisdiction is None:
            return None
        return await get_or_create_role(
            session,
            source_id=senate_seat_role_source_id(ld),
            organization_id=anchors.senate_id,
            name=_SENATE_SEAT_ROLE_NAME,
            role_type=_SENATE_SEAT_ROLE_TYPE,
            jurisdiction_id=jurisdiction.id,
            qualifier=None,
        )
    return None


async def _upsert_assignment(
    session: AsyncSession, span: TenureSpan, person: Person, role: Role
) -> Assignment:
    """Insert or update the span's Assignment by ``(source, source_id)`` — the span
    ``source_id`` is keyed on the tenure start, so an extending span updates its own row."""
    existing = (
        await session.execute(
            select(Assignment).where(
                Assignment.source == _SOURCE, Assignment.source_id == span.source_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.person_id = person.id
        existing.role_id = role.id
        existing.valid_from = span.valid_from
        existing.valid_to = span.valid_to
        existing.is_active = span.is_active
        await session.flush()
        return existing
    row = Assignment(
        source=_SOURCE,
        source_id=span.source_id,
        person_id=person.id,
        role_id=role.id,
        valid_from=span.valid_from,
        valid_to=span.valid_to,
        is_active=span.is_active,
    )
    session.add(row)
    await session.flush()
    return row


async def _ensure_citations(
    session: AsyncSession,
    assignment: Assignment,
    span: TenureSpan,
    reliability: float,
    fetch_events: dict[str, tuple[_ULID, datetime]],
) -> None:
    """Add one Citation per covered biennium the span isn't yet cited for (#78
    cite-every-biennium) — **append-only**. Provenance is write-once for the app role (the
    #54 ``REVOKE DELETE`` on ``citations``), so this never deletes/rewrites; it inserts only
    the missing biennia.

    Idempotency keys on the roster's **biennium** (the cited ``FetchEvent``'s ``resource_id``,
    ``sponsors:<biennium>``), not the ``fetch_event_id`` — the daily current-biennium re-pull
    records a *fresh* FetchEvent each run (#63/#65), so keying on the id would append a new
    citation every day. Keying on the biennium keeps exactly one citation per covered
    biennium across re-runs."""
    already_cited = set(
        (
            await session.execute(
                select(FetchEvent.resource_id)
                .join(Citation, Citation.fetch_event_id == FetchEvent.id)
                .where(
                    Citation.entity_type == _ASSIGNMENT_CITATION_TYPE,
                    Citation.entity_id == assignment.id,
                )
            )
        )
        .scalars()
        .all()
    )
    for biennium in bienniums_in_range(span.start_biennium, span.end_biennium):
        fetch_event = fetch_events.get(biennium)
        if fetch_event is None:
            continue
        if f"{SPONSORS_RESOURCE_PREFIX}{biennium}" in already_cited:
            continue  # already cited for this biennium (append-only — no rewrite)
        fetch_event_id, fetched_at = fetch_event
        session.add(
            Citation(
                entity_type=_ASSIGNMENT_CITATION_TYPE,
                entity_id=assignment.id,
                fetch_event_id=fetch_event_id,
                field_path=None,
                confidence=reliability,
                asserted_at=fetched_at,
            )
        )
    await session.flush()
