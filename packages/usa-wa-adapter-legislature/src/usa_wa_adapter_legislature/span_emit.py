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

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import Citation, FetchEvent
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from usa_wa_adapter_legislature.harvest_committee_meetings import bienniums_in_range
from usa_wa_adapter_legislature.synthesis import parse_biennium
from usa_wa_adapter_legislature.tenure_spans import TenureSpan

logger = get_logger(__name__)

SOURCE = "usa_wa_legislature"
ASSIGNMENT_CITATION_TYPE = "assignment"

#: Default mass-close guard fraction — shared by the builders and their CLI flags so the
#: operator override (`--max-close-fraction`) documents the same default everywhere.
MAX_CLOSE_FRACTION_DEFAULT = 0.5


@dataclass(frozen=True)
class StaleSweepOutcome:
    """What :func:`close_stale_spans` did — ``aborted`` distinguishes a mass-close abort
    from a clean nothing-to-close run (#83 CR: the builders surface it in their logs)."""

    closed: int = 0
    aborted: bool = False


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
    person_source: str = SOURCE,
    assignment_source: str = SOURCE,
) -> int:
    """Upsert an :class:`Assignment` per span (+ per-biennium citations); return the count.

    A span whose Person or Role can't be resolved is logged and skipped (never guessed).

    ``person_source`` / ``assignment_source`` split the two uses of ``source`` that coincide
    for the WSL callers but diverge for PDC (#79): a PDC House-position span resolves the
    **WSL-sourced** Person (``person_source='usa_wa_legislature'``) yet writes a **PDC-sourced**
    Assignment (``assignment_source='usa_wa_pdc'``), because PDC is the authority for the
    ballot Position. Both default to ``usa_wa_legislature``, so sponsor + committee callers are
    unchanged."""
    emitted = 0
    for span in spans:
        person = await resolve_person(session, span.member_id, source=person_source)
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
        assignment = await _upsert_assignment(session, span, person, role, assignment_source)
        await _ensure_citations(session, assignment, span, reliability, citation_target)
        emitted += 1
    return emitted


async def close_stale_spans(
    session: AsyncSession,
    *,
    assignment_source: str,
    kinds: Collection[str],
    asserted_source_ids: Collection[str],
    current_biennium: str,
    max_close_fraction: float = MAX_CLOSE_FRACTION_DEFAULT,
    close_fraction_floor: int = 5,
) -> StaleSweepOutcome:
    """Close open span Assignments the rebuild no longer asserts; return the outcome (#83).

    The restricted daily re-drive rebuilds only members observed in the current biennium, so
    a departed member (or a sitting member who left a committee, or a superseded-wire orphan)
    is never rebuilt and their open row would stay ``is_active=True`` forever. This sweep
    closes every ``is_active`` Assignment of ``assignment_source`` whose span ``source_id``
    (4-part ``{member}:{kind}:{discriminator}:{start}``) carries one of the builder's
    ``kinds`` but was **not** in this run's built span set — set ``is_active=False`` and
    ``valid_to`` = Dec 31 of the biennium before ``current_biennium`` (clamped to
    ``valid_from`` for a span that started in the current biennium). ``current_biennium``
    must be the biennium the asserted span set was built against — a mismatched pair would
    close everything outside the wrong cohort.

    The valid_to derivation rests on the daily cadence: a member's last rebuilt biennium is
    ``current - 1``. If the re-drive skipped a boundary, the close date lands late — the next
    unrestricted rebuild self-corrects (spans upsert on ``source_id``). Non-4-part (legacy)
    source_ids are never touched.

    **Mass-close guards.** An **empty** asserted set aborts the sweep, and so does closing
    more than ``max_close_fraction`` of the open rows of the swept kinds once past
    ``close_fraction_floor`` candidates (the #44/#56 floor pattern — a truncated-but-valid
    roster wire archived as latest must not read as mass departure, while a tiny cohort's
    legitimate 1-of-1 close stays under the floor). A wrongly-aborted sweep self-heals: the
    next full read re-asserts the survivors and the stale rows close then. A *legitimate*
    mass close (e.g. a wholesale WSL committee-Id re-key) needs the operator override — the
    builders/CLIs forward a raised ``max_close_fraction`` (``--max-close-fraction 1.0``).
    The outcome's ``aborted`` flag distinguishes the fraction abort from nothing-to-close;
    the empty-assertion skip is not flagged (an empty span set is a legitimate no-op for
    e.g. a Senate-only PDC run and is separately logged)."""
    if not asserted_source_ids:
        logger.warning(
            "stale_span_sweep_skipped_empty_assertion",
            extra={"assignment_source": assignment_source, "kinds": sorted(kinds)},
        )
        return StaleSweepOutcome()
    prior_end = date(parse_biennium(current_biennium)[0] - 1, 12, 31)
    asserted = set(asserted_source_ids)
    kind_set = set(kinds)
    open_rows = (
        (
            await session.execute(
                select(Assignment).where(
                    Assignment.source == assignment_source, Assignment.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    in_scope = [
        row
        for row in open_rows
        if len(parts := row.source_id.split(":")) == 4 and parts[1] in kind_set
    ]
    stale = [row for row in in_scope if row.source_id not in asserted]
    if len(stale) > close_fraction_floor and len(stale) > max_close_fraction * len(in_scope):
        logger.warning(
            "stale_span_sweep_aborted_mass_close",
            extra={
                "assignment_source": assignment_source,
                "kinds": sorted(kind_set),
                "stale": len(stale),
                "open": len(in_scope),
                "max_close_fraction": max_close_fraction,
            },
        )
        return StaleSweepOutcome(aborted=True)
    for row in stale:
        row.is_active = False
        row.valid_to = max(prior_end, row.valid_from)
        logger.info(
            "stale_span_closed",
            extra={"source_id": row.source_id, "valid_to": row.valid_to.isoformat()},
        )
    if stale:
        await session.flush()
    return StaleSweepOutcome(closed=len(stale))


async def resolve_person(
    session: AsyncSession, member_id: str, *, source: str = SOURCE
) -> Person | None:
    """The :class:`Person` a span's ``member_id`` names (``(source, source_id)``). ``source``
    defaults to WSL — every span binds a WSL-sourced Person, PDC spans included (#79)."""
    return (
        await session.execute(
            select(Person).where(Person.source == source, Person.source_id == member_id)
        )
    ).scalar_one_or_none()


async def _upsert_assignment(
    session: AsyncSession,
    span: TenureSpan,
    person: Person,
    role: Role,
    assignment_source: str,
) -> Assignment:
    """Insert or update the span's Assignment by ``(source, source_id)`` — the span
    ``source_id`` is keyed on the tenure start, so an extending span updates its own row."""
    existing = (
        await session.execute(
            select(Assignment).where(
                Assignment.source == assignment_source, Assignment.source_id == span.source_id
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
        source=assignment_source,
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
