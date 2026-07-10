"""PDC House-position span emission + identifier links (#79).

Binds the House-position :class:`~usa_wa_adapter_legislature.tenure_spans.TenureSpan`s to the
generic emitter (:mod:`usa_wa_adapter_legislature.span_emit`) with the **PDC source split**: a
House seat Assignment is ``usa_wa_pdc``-sourced (PDC is the authority for the ballot Position)
but binds the **WSL**-sourced :class:`Person`. The seat Role (``state_representative``, keyed
on ``(LD, Position)``) is get-or-created ``usa_wa_legislature`` — a seat is legislature
structure, symmetric with the Senate seat Role P1b emits.

Each biennium of a span cites that biennium's ``house-winners:<Y>`` cohort (the driver maps
biennium → the archived cohort's FetchEvent). ``person_wa_pdc`` identifiers are emitted
separately (:func:`emit_pdc_identifiers`) — they are per-Person, not per-tenure, so they don't
flow through the span builder.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import PersonIdentifier, Role
from usa_wa_adapter_legislature.normalize.members import get_or_create_role, resolve_ld_jurisdiction
from usa_wa_adapter_legislature.span_emit import CitationTarget, emit_spans, resolve_person
from usa_wa_adapter_legislature.tenure_spans import TenureSpan
from usa_wa_adapter_pdc.normalize.positions import (
    PDC_PERSON_ID_SCHEME,
    PDC_SOURCE,
    house_seat_role_source_id,
    parse_house_span_discriminator,
    pdc_person_identifier_source_id,
)

logger = get_logger(__name__)

_WSL_SOURCE = "usa_wa_legislature"
_HOUSE_SEAT_ROLE_TYPE = "state_representative"
_HOUSE_SEAT_ROLE_NAME = "State Representative"

#: ``biennium -> (fetch_event_id, fetched_at, resource_id)`` — the ``house-winners:<Y>`` cohort
#: attesting one biennium of a House Position span.
HouseCitationEvents = dict[str, CitationTarget]


async def emit_house_position_spans(
    session: AsyncSession,
    spans: list[TenureSpan],
    *,
    anchors: object,
    reliability: float,
    fetch_events: HouseCitationEvents,
) -> int:
    """Upsert one ``usa_wa_pdc`` Assignment per House Position span; return the count."""

    async def _resolve_role(session: AsyncSession, span: TenureSpan) -> Role | None:
        ld, qualifier = parse_house_span_discriminator(span.discriminator)
        jurisdiction = await resolve_ld_jurisdiction(session, ld)
        if jurisdiction is None:
            logger.warning("pdc_span_unsynced_ld", extra={"ld": ld, "member_id": span.member_id})
            return None
        return await get_or_create_role(
            session,
            source_id=house_seat_role_source_id(ld, qualifier),
            organization_id=anchors.house_id,
            name=_HOUSE_SEAT_ROLE_NAME,
            role_type=_HOUSE_SEAT_ROLE_TYPE,
            jurisdiction_id=jurisdiction.id,
            qualifier=qualifier,
        )

    def _citation_target(_span: TenureSpan, biennium: str) -> CitationTarget | None:
        return fetch_events.get(biennium)

    return await emit_spans(
        session,
        spans,
        resolve_role=_resolve_role,
        citation_target=_citation_target,
        reliability=reliability,
        person_source=_WSL_SOURCE,
        assignment_source=PDC_SOURCE,
    )


async def emit_pdc_identifiers(session: AsyncSession, links: Iterable[tuple[str, str]]) -> int:
    """Attach a ``person_wa_pdc`` child :class:`PersonIdentifier` per ``(member_id, pdc_id)``
    link — the cross-source identity link (#69/#74/#75). Idempotent: dedups within the call
    and skips a ``source_id`` already present (append-only, no rewrite). A link whose WSL
    Person isn't ingested is skipped + logged. Returns the number newly added."""
    added = 0
    seen: set[str] = set()
    for member_id, pdc_id in links:
        source_id = pdc_person_identifier_source_id(pdc_id)
        if source_id in seen:
            continue
        seen.add(source_id)
        existing = (
            await session.execute(
                select(PersonIdentifier).where(
                    PersonIdentifier.source == PDC_SOURCE,
                    PersonIdentifier.source_id == source_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        person = await resolve_person(session, member_id, source=_WSL_SOURCE)
        if person is None:
            logger.warning("pdc_identifier_person_absent", extra={"member_id": member_id})
            continue
        session.add(
            PersonIdentifier(
                source=PDC_SOURCE,
                source_id=source_id,
                person_id=person.id,
                scheme=PDC_PERSON_ID_SCHEME,
                value=pdc_id,
            )
        )
        await session.flush()
        added += 1
    return added
