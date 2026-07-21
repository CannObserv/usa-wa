"""Committee-membership span→Assignment emission (#82).

Binds committee-membership :class:`~usa_wa_adapter_legislature.tenure_spans.TenureSpan`s to
the generic emitter (:mod:`span_emit`) by supplying the two kind-specific pieces:

- **Role resolution** — a ``committee`` span binds the committee Org's shared ``member``
  Role (power-map#269; WSL exposes no chair/vice-chair, so every member is a plain member).
  The Org is resolved by the span's discriminator (the committee's stable WSL ``Id``); a
  committee never ingested (e.g. a historical body outside the roster archive) resolves to
  ``None`` and the span is skipped + logged, never guessed.
- **Citation target** — one archived roster per **(biennium, committee)**, not per biennium:
  each committee's roster is its own ``committee-members-hist:<biennium>:<id>:…`` pull.

See :mod:`span_emit` for the append-only, write-once provenance contract (#54).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Organization, Role
from usa_wa_adapter_legislature.committee_membership_observations import KIND_COMMITTEE
from usa_wa_adapter_legislature.normalize.members import (
    committee_member_role_source_id,
    get_or_create_role,
)
from usa_wa_adapter_legislature.span_emit import SOURCE, CitationTarget, emit_spans
from usa_wa_adapter_legislature.tenure_spans import TenureSpan

logger = get_logger(__name__)

_MEMBER_ROLE_NAME = "Member"
# PM's role_types catalog refines the generic `member` into per-kind slugs; a committee
# membership is `committee_member` (power-map#268). Emitting the generic `member` left the
# role's classifier permanently diverged from PM's `role_type_slug`, so the #109 no-op gate
# read a genuine diff and re-enqueued every reconcile forever (usa-wa#110). Emit the catalog
# slug so the observation matches PM and the gate converges.
_MEMBER_ROLE_TYPE = "committee_member"

#: ``(biennium, committee_source_id) -> (fetch_event_id, fetched_at, resource_id)``
CommitteeCitationEvents = dict[tuple[str, str], CitationTarget]


async def emit_committee_spans(
    session: AsyncSession,
    spans: list[TenureSpan],
    *,
    reliability: float,
    fetch_events: CommitteeCitationEvents,
) -> int:
    """Upsert an :class:`Assignment` per committee-membership span; return the count."""

    def _citation_target(span: TenureSpan, biennium: str) -> CitationTarget | None:
        return fetch_events.get((biennium, span.discriminator))

    return await emit_spans(
        session,
        spans,
        resolve_role=_resolve_committee_role,
        citation_target=_citation_target,
        reliability=reliability,
    )


async def _resolve_committee_role(session: AsyncSession, span: TenureSpan) -> Role | None:
    """The committee's shared ``member`` Role. ``None`` when the span isn't a committee
    tenure, or the committee Org was never ingested (logged, skipped — not guessed)."""
    if span.kind != KIND_COMMITTEE:
        return None
    committee_source_id = span.discriminator
    committee = (
        await session.execute(
            select(Organization).where(
                Organization.source == SOURCE,
                Organization.source_id == committee_source_id,
            )
        )
    ).scalar_one_or_none()
    if committee is None:
        logger.warning(
            "committee_span_unknown_committee",
            extra={"committee_source_id": committee_source_id, "member_id": span.member_id},
        )
        return None
    return await get_or_create_role(
        session,
        source_id=committee_member_role_source_id(committee_source_id),
        organization_id=committee.id,
        name=_MEMBER_ROLE_NAME,
        role_type=_MEMBER_ROLE_TYPE,
    )
