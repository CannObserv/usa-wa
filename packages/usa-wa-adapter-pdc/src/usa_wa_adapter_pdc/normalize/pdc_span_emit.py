"""PDC ``person_wa_pdc`` identifier links (#79; identifier-only since #101).

The idempotent ``person_wa_pdc`` child-identifier upsert — PDC's demoted contribution since the
#101 re-partition: a cross-source link attaching a PDC filer id to the WSL-sourced :class:`Person`
(the #69/#74/#75 links). The House Position **seat** emission moved to
:mod:`usa_wa_adapter_sos.house_span_emit` when SOS became the seat authority (#101); PDC no longer
emits the seat.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import PersonIdentifier
from usa_wa_adapter_legislature.span_emit import resolve_person
from usa_wa_adapter_pdc.normalize.positions import (
    PDC_PERSON_ID_SCHEME,
    PDC_SOURCE,
    pdc_person_identifier_source_id,
)

logger = get_logger(__name__)

_WSL_SOURCE = "usa_wa_legislature"


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
