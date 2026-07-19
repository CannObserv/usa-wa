"""WSL+SOS House Position span emission (#101) — spans → merged usa_wa_legislature Assignments.

Binds the House-position :class:`~usa_wa_adapter_legislature.tenure_spans.TenureSpan`s to the
generic emitter (:mod:`usa_wa_adapter_legislature.span_emit`): one Assignment per contiguous House
Position tenure, bound to the WSL-sourced :class:`Person` and the ``state_representative`` seat
Role (keyed on ``(LD, Position)``, get-or-created ``usa_wa_legislature``). The Assignment ``source``
defaults to ``usa_wa_legislature`` — a seat is legislature structure, symmetric with the Senate
seat (#75); PDC was the pre-#101 authority (``usa_wa_pdc``) and the re-source migration flips those
rows.

Each biennium of a span cites that biennium's attesting cohort, supplied by the driver via
``fetch_events`` — the WSL+SOS builder passes the ``sos-legresults:<YYYYMMDD>`` results cohort
(the Position authority) — **except** an elimination-inferred ``(member, biennium)`` (#103),
which cites the WSL sponsor roster (``roster_events``) instead: the SOS wire never names an
appointee, so the roster is the document that actually places the member in the LD.
``person_wa_pdc`` identifier links are a *separate* concern that stays in
:mod:`usa_wa_adapter_pdc.normalize.pdc_span_emit` (they are PDC's, per-Person, not per-tenure).

Homed in the SOS package because SOS owns the House Position seat since #101; it reuses the PDC
seat-Role source-id / discriminator helpers (a Layer-3 sibling import, one-directional).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from usa_wa_adapter_pdc.normalize.positions import (
    house_seat_role_source_id,
    parse_house_span_discriminator,
)

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Role
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors
from usa_wa_adapter_legislature.normalize.members import get_or_create_role, resolve_ld_jurisdiction
from usa_wa_adapter_legislature.span_emit import CitationTarget, emit_spans
from usa_wa_adapter_legislature.tenure_spans import TenureSpan

logger = get_logger(__name__)

_PERSON_SOURCE = "usa_wa_legislature"
_HOUSE_ASSIGNMENT_SOURCE = "usa_wa_legislature"
_HOUSE_SEAT_ROLE_TYPE = "state_representative"
_HOUSE_SEAT_ROLE_NAME = "State Representative"

#: ``biennium -> (fetch_event_id, fetched_at, resource_id)`` — the cohort attesting one biennium
#: of a House Position span (``sos-legresults:<YYYYMMDD>`` for the WSL+SOS builder; the sponsor
#: roster ``sponsors:<biennium>`` for an inferred biennium, #103).
HouseCitationEvents = dict[str, CitationTarget]


async def emit_house_position_spans(
    session: AsyncSession,
    spans: list[TenureSpan],
    *,
    anchors: BootstrapAnchors,
    reliability: float,
    fetch_events: HouseCitationEvents,
    roster_events: HouseCitationEvents | None = None,
    inferred_keys: set[tuple[str, str]] | None = None,
    assignment_source: str = _HOUSE_ASSIGNMENT_SOURCE,
) -> int:
    """Upsert one Assignment per House Position span; return the count.

    ``assignment_source`` defaults to ``usa_wa_legislature`` (the seat's authority since #101);
    the seat Role stays ``usa_wa_legislature`` and the Person is WSL's regardless. An
    ``inferred_keys`` ``(member_id, biennium)`` pair cites that biennium's ``roster_events``
    entry instead of ``fetch_events`` (#103 — the roster wire is the one naming the member),
    falling back to the SOS cohort only if the roster wasn't archived."""

    async def _resolve_role(session: AsyncSession, span: TenureSpan) -> Role | None:
        ld, qualifier = parse_house_span_discriminator(span.discriminator)
        jurisdiction = await resolve_ld_jurisdiction(session, ld)
        if jurisdiction is None:
            logger.warning("house_span_unsynced_ld", extra={"ld": ld, "member_id": span.member_id})
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

    inferred = inferred_keys or frozenset()
    rosters = roster_events or {}

    def _citation_target(span: TenureSpan, biennium: str) -> CitationTarget | None:
        if (span.member_id, biennium) in inferred:
            return rosters.get(biennium) or fetch_events.get(biennium)
        return fetch_events.get(biennium)

    return await emit_spans(
        session,
        spans,
        resolve_role=_resolve_role,
        citation_target=_citation_target,
        reliability=reliability,
        person_source=_PERSON_SOURCE,
        assignment_source=assignment_source,
    )
