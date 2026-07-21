"""Sponsor span→Assignment emission (#78 Phase B) — party + Senate-seat tenure.

Binds sponsor-derived :class:`~usa_wa_adapter_legislature.tenure_spans.TenureSpan`s to the
generic emitter (:mod:`span_emit`) by supplying the two kind-specific pieces:

- **Role resolution** — a ``party`` span binds the matching Party Org's shared ``member``
  Role; a ``chamber-senate`` span binds the ``(Senate, state_senator, LD)`` seat Role (#68).
- **Citation target** — one archived roster per biennium (``sponsors:<biennium>``).

The Assignment source stays ``usa_wa_legislature`` (a legislature-structural fact). See
:mod:`span_emit` for the append-only, write-once provenance contract (#54).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Role
from usa_wa_adapter_legislature.adapter import SPONSORS_RESOURCE_PREFIX
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors
from usa_wa_adapter_legislature.normalize.members import (
    get_or_create_role,
    party_role_source_id,
    resolve_ld_jurisdiction,
    senate_seat_role_source_id,
)
from usa_wa_adapter_legislature.span_emit import CitationTarget, emit_spans
from usa_wa_adapter_legislature.sponsor_observations import KIND_PARTY, KIND_SENATE
from usa_wa_adapter_legislature.tenure_spans import TenureSpan

logger = get_logger(__name__)

_MEMBER_ROLE_NAME = "Member"
# The party-membership Role's PM classifier is `party_member`, not the generic `member`
# (power-map#268 catalog). Emitting `member` diverged it from PM's `role_type_slug` and
# armed the #109 no-op-gate churn (usa-wa#110); emit the catalog slug so the gate converges.
_MEMBER_ROLE_TYPE = "party_member"
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
    """Upsert an :class:`Assignment` per party/Senate-seat span; return the count.

    ``fetch_events`` maps ``biennium → (fetch_event_id, fetched_at)`` (from the cohort
    provider) — each biennium's ``sponsors:<biennium>`` roster attests the span."""

    async def _resolve_role(session: AsyncSession, span: TenureSpan) -> Role | None:
        return await _resolve_sponsor_role(session, span, anchors)

    def _citation_target(_span: TenureSpan, biennium: str) -> CitationTarget | None:
        event = fetch_events.get(biennium)
        if event is None:
            return None
        fetch_event_id, fetched_at = event
        return (fetch_event_id, fetched_at, f"{SPONSORS_RESOURCE_PREFIX}{biennium}")

    return await emit_spans(
        session,
        spans,
        resolve_role=_resolve_role,
        citation_target=_citation_target,
        reliability=reliability,
    )


async def _resolve_sponsor_role(
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
