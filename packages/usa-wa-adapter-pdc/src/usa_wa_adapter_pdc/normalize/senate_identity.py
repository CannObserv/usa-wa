"""Senate-identity normalizer — PDC Senate winners → `person_wa_pdc` (#75).

The Senate counterpart to the House-position normalizer (#69), but **identifier-only**:
WSL's P1b sponsors normalizer already emits the Senate seat Role + Assignment (a single
seat per LD, no ballot Position for PDC to add), so PDC's Senate contribution is purely
the cross-source identity link. Each PDC Senate winner is matched to the *existing* WSL
Senate :class:`Person` — within its LD, by folded surname (single seat/LD → the match is
effectively unique) — and gains a `person_wa_pdc` child :class:`PersonIdentifier` that the
person descriptor carries to PM as an `additional_identifier` (deterministic PDC↔WSL link,
no name-match reliance).

**Robustness check on WSL.** PDC is an independent record of *who won*. A winner with no
matching WSL senator in its LD (`pdc_senate_unresolved`) or a matched roster member whose
WSL Person isn't ingested yet (`pdc_senate_person_absent`) is surfaced as a log — the
discrepancy signal the issue asked for. (Appointed senators, who never won an election,
simply have no PDC winner row; the forward per-winner check doesn't flag them.)

The WSL Senate roster (`{LD: [SenateEntry]}`) is built by the caller from a `GetSponsors`
pull via :func:`~usa_wa_adapter_pdc.normalize.house_positions.build_senate_roster` — the
same roster the #74 mover inference uses.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.adapter import FetchedPayload, NormalizedBatch
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import PersonIdentifier
from usa_wa_adapter_legislature.normalize.members import EntityCollector, district_number
from usa_wa_adapter_pdc.normalize.house_positions import SenateEntry
from usa_wa_adapter_pdc.normalize.persons import resolve_wsl_person
from usa_wa_adapter_pdc.normalize.positions import (
    PDC_PERSON_ID_SCHEME,
    PDC_SOURCE,
    pdc_person_identifier_source_id,
    surname_match_set,
)

logger = get_logger(__name__)


async def normalize_senate_identities(
    payload: FetchedPayload,
    *,
    senate_roster: dict[int, list[SenateEntry]],
    session: AsyncSession,
) -> NormalizedBatch:
    """Emit `person_wa_pdc` identifiers for the PDC Senate winner cohort (#75).

    Identifier-only — no Role/Assignment. Returns the matched identifiers; unmatched or
    not-yet-ingested winners are logged (the WSL robustness signal), not emitted."""
    winners = payload.parsed or []
    collector = EntityCollector()

    for row in winners:
        pdc_id = str(row.get("person_id") or "").strip()
        ld = district_number(row.get("legislative_district"))
        if not pdc_id or ld is None:
            logger.warning(
                "pdc_senate_row_incomplete",
                extra={"person_id": pdc_id, "ld": row.get("legislative_district")},
            )
            continue

        keys = surname_match_set(row.get("filer_name") or "")
        candidates = [s for s in senate_roster.get(ld, []) if s.folded_last in keys]
        if len(candidates) != 1:
            # PDC says someone won this LD's Senate seat, but the WSL roster has no unique
            # match — a WSL discrepancy (missing senator / name mismatch), not a guess.
            logger.info(
                "pdc_senate_unresolved",
                extra={
                    "ld": ld,
                    "filer_name": row.get("filer_name"),
                    "candidates": len(candidates),
                },
            )
            continue

        person = await resolve_wsl_person(session, candidates[0].member_id)
        if person is None:
            logger.warning(
                "pdc_senate_person_absent",
                extra={"member_id": candidates[0].member_id, "ld": ld},
            )
            continue

        collector.add(
            PersonIdentifier(
                source=PDC_SOURCE,
                source_id=pdc_person_identifier_source_id(pdc_id),
                person_id=person.id,
                scheme=PDC_PERSON_ID_SCHEME,
                value=pdc_id,
            )
        )

    return NormalizedBatch(entities=collector.entities, citations=[])
