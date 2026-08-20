"""Roster span→Assignment emission (#228 Phase B) — minted-member tenure.

Binds roster-derived spans to the generic emitter the way :mod:`sponsors.emit` binds
wire-derived ones, with three deliberate differences:

* **Person space**: a minted member's Person lives under ``usa_wa_legislature_roster``
  (``person_source``), never the WSL space — the WSL-joined identities' spans go through
  the *sponsor* emission (deepened, #228), not here.
* **Assignment source**: the roster is the authority for these facts, so the Assignment
  carries the roster source too (the PDC split precedent, #79).
* **Citation**: one archived edition attests every biennium of every span. The sponsor
  path cites one roster *per biennium*; here the citation dedup on the shared resource key
  collapses to one row per Assignment, which is the honest shape — one document is the
  evidence.

Role resolution is shared with the sponsor path
(:func:`~usa_wa_adapter_legislature.sponsors.emit.resolve_span_role`), so the party and
Senate-seat Role keys stay single-sourced.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Role
from clearinghouse_domain_legislative.span_emit import CitationTarget, emit_spans
from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.sponsors.emit import resolve_span_role

logger = get_logger(__name__)


async def emit_roster_spans(
    session: AsyncSession,
    spans: list[TenureSpan],
    *,
    anchors: BootstrapAnchors,
    reliability: float,
    citation: CitationTarget,
) -> int:
    """Upsert an Assignment per minted-member span; return the count.

    ``citation`` is the archived roster edition's ``(fetch_event_id, fetched_at,
    resource_id)`` — the one target every biennium of every span cites.
    """

    async def _resolve_role(session: AsyncSession, span: TenureSpan) -> Role | None:
        return await resolve_span_role(session, span, anchors)

    def _citation_target(_span: TenureSpan, _biennium: str) -> CitationTarget | None:
        return citation

    return await emit_spans(
        session,
        spans,
        resolve_role=_resolve_role,
        citation_target=_citation_target,
        reliability=reliability,
        person_source=ROSTER_SOURCE_SLUG,
        assignment_source=ROSTER_SOURCE_SLUG,
    )
