"""Generic span→Assignment emission (#82, extracted from the #78 sponsor emitter).

Given merged :class:`~usa_wa_adapter_legislature.tenure_spans.TenureSpan`s, resolve each
span's :class:`Person` + Role and upsert one :class:`Assignment` per tenure carrying the
span's ``valid_from/valid_to/is_active``. What differs per tenure *kind* is injected:

- ``resolve_role(session, span) -> Role | None`` — how a span's ``kind``/``discriminator``
  binds to a Role (a party Org's ``member`` Role, a Senate seat, a committee's ``member``
  Role). Returning ``None`` skips the span (never guessed).
- ``citation_target(span, biennium) -> (fetch_event_id, fetched_at, resource_id) | None`` —
  which archived pull attests the span for that biennium. Sponsor spans cite one roster per
  biennium (``sponsors:<biennium>``); committee-membership spans cite a roster per
  *(biennium, committee)* (``committee-members-hist:<biennium>:<id>``), which is why the
  target is a callable rather than a biennium-keyed dict.

**Provenance — cite every biennium in range, append-only.** A merged span is attested by
every biennium's roster it was observed in, so it carries one :class:`Citation` per covered
biennium (a span is a contiguous run, so its biennia = ``bienniums_in_range(start, end)``).
Re-emission is **insert-only**: provenance is write-once for the app role (#54 ``REVOKE
DELETE`` on ``citations``), and the already-cited check keys on the attesting FetchEvent's
``resource_id`` — not its id — because a daily re-pull mints a fresh FetchEvent for the same
resource (#63/#65), so id-keying would append a citation every run.

Direct session writes (Phase-B derived rows, like the reconcilers/heal) — no AdapterRunner.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import Citation, FetchEvent
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from usa_wa_adapter_legislature.harvest_committee_meetings import bienniums_in_range
from usa_wa_adapter_legislature.tenure_spans import TenureSpan

logger = get_logger(__name__)

SOURCE = "usa_wa_legislature"
ASSIGNMENT_CITATION_TYPE = "assignment"

#: ``(fetch_event_id, fetched_at, resource_id)`` — the archived pull attesting one biennium
#: of a span. ``resource_id`` is the append-only citation's idempotency key.
CitationTarget = tuple[_ULID, datetime, str]

RoleResolver = Callable[[AsyncSession, TenureSpan], Awaitable[Role | None]]
CitationLocator = Callable[[TenureSpan, str], CitationTarget | None]


async def emit_spans(
    session: AsyncSession,
    spans: list[TenureSpan],
    *,
    resolve_role: RoleResolver,
    citation_target: CitationLocator,
    reliability: float,
) -> int:
    """Upsert an :class:`Assignment` per span (+ per-biennium citations); return the count.

    A span whose Person or Role can't be resolved is logged and skipped (never guessed)."""
    emitted = 0
    for span in spans:
        person = await resolve_person(session, span.member_id)
        if person is None:
            logger.warning("span_person_absent", extra={"member_id": span.member_id})
            continue
        role = await resolve_role(session, span)
        if role is None:
            logger.info(
                "span_role_unresolved",
                extra={"member_id": span.member_id, "kind": span.kind, "disc": span.discriminator},
            )
            continue
        assignment = await _upsert_assignment(session, span, person, role)
        await _ensure_citations(session, assignment, span, reliability, citation_target)
        emitted += 1
    return emitted


async def resolve_person(session: AsyncSession, member_id: str) -> Person | None:
    """The WSL :class:`Person` a span's ``member_id`` names (``(source, source_id)``)."""
    return (
        await session.execute(
            select(Person).where(Person.source == SOURCE, Person.source_id == member_id)
        )
    ).scalar_one_or_none()


async def _upsert_assignment(
    session: AsyncSession, span: TenureSpan, person: Person, role: Role
) -> Assignment:
    """Insert or update the span's Assignment by ``(source, source_id)`` — the span
    ``source_id`` is keyed on the tenure start, so an extending span updates its own row."""
    existing = (
        await session.execute(
            select(Assignment).where(
                Assignment.source == SOURCE, Assignment.source_id == span.source_id
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
        source=SOURCE,
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
    citation_target: CitationLocator,
) -> None:
    """Add one Citation per covered biennium the span isn't yet cited for — append-only
    (see the module docstring: #54 write-once provenance, resource_id-keyed idempotency)."""
    already_cited = set(
        (
            await session.execute(
                select(FetchEvent.resource_id)
                .join(Citation, Citation.fetch_event_id == FetchEvent.id)
                .where(
                    Citation.entity_type == ASSIGNMENT_CITATION_TYPE,
                    Citation.entity_id == assignment.id,
                )
            )
        )
        .scalars()
        .all()
    )
    for biennium in bienniums_in_range(span.start_biennium, span.end_biennium):
        target = citation_target(span, biennium)
        if target is None:
            continue
        fetch_event_id, fetched_at, resource_id = target
        if resource_id in already_cited:
            continue  # already cited for this biennium's roster (append-only — no rewrite)
        session.add(
            Citation(
                entity_type=ASSIGNMENT_CITATION_TYPE,
                entity_id=assignment.id,
                fetch_event_id=fetch_event_id,
                field_path=None,
                confidence=reliability,
                asserted_at=fetched_at,
            )
        )
    await session.flush()
