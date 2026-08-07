"""Senate odd-year ballot corroboration + citation (#123 §2) — the SOS Senate consumers.

The Senate seat is built ``usa_wa_legislature``-sourced by the WSL sponsor span builder; SOS only
**consumes** its ballot evidence, so this lives in the SOS package (SOS→legislature, never the
reverse — the layer the ``house/`` application respects too). It runs after the WSL + SOS refreshes
have rebuilt the open-seat cohort and archived the current results wire.

Two consumers of the odd-year ``senate_winners()`` cohort (#106 A′), the mid-biennium special that
seats a senator with no automatic wire signal (Hunt, LD5, appointed June 2025 then **elected** Nov
2025 to the unexpired term):

- **2a citation.** An elected senator's open Senate span carries no ballot citation distinguishing
  her *elected* status from her *appointed* one (the operator ``seated`` event dates the boundary,
  #107). :func:`cite_elected_senators` adds a field-level citation on the span's ``valid_from`` to
  the ``sos-legresults:<odd>`` wire — attestation, **not** a boundary move (the Nov win does not
  move the June appointment date; tenure is continuous). Idempotent (``add_field_citation`` dedups).

- **2b corroboration (the higher-value half).** An odd-year Senate winner with **no open Senate
  span at that LD** is a **missing operator event** — the silent failure the succession invariants
  exist to catch, which the chamber-count gate only detects once the count has already drifted. The
  asymmetry cuts toward the Senate: a House gap self-heals through the #103 elimination; a Senate
  gap stays silent. :func:`missing_winner_lds` names every such LD; the CLI exits 1 so the
  ``OnFailure=`` handler emails the operator.

  **The gate keys on seat *existence*, not occupant identity — deliberately.** An LD whose seat is
  held by *someone other than the ballot winner* (a name change, or a predecessor not yet succeeded)
  is reported as ``mismatched`` but does **not** fail the gate: a surname mismatch is more often a
  legitimate ballot↔roster name divergence than a real missing succession, and failing on it would
  page the operator on every senator who changed their name. The mismatch is surfaced (logged +
  ``mismatched_lds``) for a human to judge, not gated. Only a *wholly unoccupied* winner LD — the
  unambiguous missing ``seated`` — exits 1.

    python -m usa_wa_adapter_sos.senate_corroboration

Citation is app-role DML (a ``Citation`` insert); the corroboration is read-only. Exit 0 clean /
1 on a missing winner / 2 on a config error.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from ulid import ULID as _ULID

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction
from usa_wa_adapter_legislature.span_emit import (
    ASSIGNMENT_CITATION_TYPE,
    CitationTarget,
    add_field_citation,
)
from usa_wa_adapter_legislature.synthesis import biennium_for_date
from usa_wa_adapter_pdc.adapter import election_years_for_biennium
from usa_wa_adapter_pdc.normalize.positions import surname_match_set
from usa_wa_adapter_sos.positions import SenateWinner
from usa_wa_adapter_sos.provisioning import get_or_create_results_source
from usa_wa_adapter_sos.results.cohort import SosResultsCohortProvider

logger = get_logger(__name__)

#: The ``state_senator`` seat Role ``source_id`` prefix (one seat per LD, ``seat:senate:ld-{n}``).
_SENATE_SEAT_PREFIX = "seat:senate:ld-"
_SENATOR = "state_senator"

#: ``valid_from`` — the Senate span field an elected-senator ballot line attests (2a). The Nov win
#: does not *move* the appointment boundary (#107); it corroborates the tenure's start.
_CITED_FIELD = "valid_from"


@dataclass(frozen=True)
class SenateOccupant:
    """One open ``state_senator`` occupant at an LD — the corroboration target."""

    assignment_id: _ULID
    name_full: str
    valid_from: date


@dataclass
class SenateCorroborationResult:
    """Outcome of one daily run. ``ok`` is the exit gate (0 iff no missing winner)."""

    odd_year: int
    winners: int = 0
    citations_added: int = 0
    missing_lds: list[int] = field(default_factory=list)
    mismatched_lds: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_lds


def _ld_from_role_source_id(source_id: str) -> int | None:
    """The LD number of a Senate seat Role ``source_id`` (``seat:senate:ld-5`` → ``5``), else
    ``None`` for any other role key."""
    if not source_id.startswith(_SENATE_SEAT_PREFIX):
        return None
    tail = source_id[len(_SENATE_SEAT_PREFIX) :]
    return int(tail) if tail.isdigit() else None


async def open_senate_occupants(session: AsyncSession) -> dict[int, list[SenateOccupant]]:
    """``{LD: [SenateOccupant]}`` for every **open** ``state_senator`` seat — the live cohort the
    odd-year winners are corroborated against.

    Open = ``is_active`` with both lifecycle tombstones NULL (the succession-invariant open-cohort
    definition). Keyed on the seat Role ``source_id`` (``seat:senate:ld-{n}``), so a role with any
    other key is ignored. A doubly-occupied seat yields a two-element list — a distinct violation
    the succession invariants already assert; here it simply means both are candidate citation
    targets."""
    rows = (
        await session.execute(
            select(Role.source_id, Assignment.id, Person.name_full, Assignment.valid_from)
            .join(Assignment, Assignment.role_id == Role.id)
            .join(Person, Person.id == Assignment.person_id)
            .where(
                Role.role_type == _SENATOR,
                Assignment.is_active.is_(True),
                Assignment.deleted_at.is_(None),
                Assignment.archived_at.is_(None),
            )
        )
    ).all()
    by_ld: dict[int, list[SenateOccupant]] = {}
    for source_id, assignment_id, name_full, valid_from in rows:
        ld = _ld_from_role_source_id(source_id)
        if ld is None:
            continue
        by_ld.setdefault(ld, []).append(
            SenateOccupant(assignment_id=assignment_id, name_full=name_full, valid_from=valid_from)
        )
    return by_ld


def missing_winner_lds(
    winners: dict[int, SenateWinner], occupants: dict[int, list[SenateOccupant]]
) -> list[int]:
    """The LDs an odd-year Senate winner names with **no open occupant** (#123 §2b) — a missing
    operator ``seated`` event. Pure, so the gate is unit-testable without a live DB."""
    return sorted(ld for ld in winners if not occupants.get(ld))


def _winner_occupant_matches(
    winner: SenateWinner, occupants: list[SenateOccupant]
) -> list[SenateOccupant]:
    """The open occupants whose folded surname set intersects the winner's — i.e. the seat is held
    by the person the ballot names. An occupant present but *not* matching is a stale/mismatched
    seat (a name change or a missing succession), reported separately, never cited."""
    return [o for o in occupants if surname_match_set(o.name_full) & winner.name_keys]


async def cite_elected_senators(
    session: AsyncSession,
    winners: dict[int, SenateWinner],
    occupants: dict[int, list[SenateOccupant]],
    *,
    target: CitationTarget,
    confidence: float,
) -> tuple[int, list[int]]:
    """Add a ``valid_from`` field citation to each odd-year winner's open Senate span (#123 §2a).

    Only an occupant whose surname matches the ballot winner is cited (so a stale occupant isn't
    mis-attributed the win). Returns ``(citations_added, mismatched_lds)`` — the latter are LDs with
    an open occupant that does **not** match the winner (surfaced, not cited). Idempotent across
    daily re-drives via :func:`add_field_citation`'s ``(entity, field, resource)`` dedup."""
    added = 0
    mismatched: list[int] = []
    for ld, winner in winners.items():
        occ = occupants.get(ld) or []
        matched = _winner_occupant_matches(winner, occ)
        if not matched:
            if occ:
                mismatched.append(ld)
            continue
        for occupant in matched:
            if await add_field_citation(
                session,
                entity_type=ASSIGNMENT_CITATION_TYPE,
                entity_id=occupant.assignment_id,
                field_path=_CITED_FIELD,
                target=target,
                confidence=confidence,
            ):
                added += 1
    return added, sorted(mismatched)


async def corroborate_senate_winners(
    session: AsyncSession, *, biennium: str | None = None
) -> SenateCorroborationResult:
    """Cite (2a) + corroborate (2b) the current biennium's **odd-year** Senate winners.

    The odd November is the mid-biennium special that seats a senator with no wire signal; the even
    seating year's winners are already dated by the sponsor roster, so this consumes only the odd
    cohort (``election_years_for_biennium(...)[-1]``). An unarchived / not-yet-held odd cohort is an
    empty winner set → a clean no-op (0 winners, ok)."""
    jurisdiction = await resolve_jurisdiction(session)
    sos_source = await get_or_create_results_source(session, jurisdiction)
    date_current = biennium_for_date(datetime.now(UTC).date())
    current = biennium or date_current
    if current != date_current:
        # A stale $USA_WA_BIENNIUM / --biennium pin would silently gate the wrong biennium's
        # winners with no breadcrumb — mirror the WSL/PDC/SOS refreshes' non-current warning.
        logger.warning(
            "senate_corroboration_noncurrent_biennium",
            extra={"biennium": current, "date_current": date_current},
        )
    odd_year = election_years_for_biennium(current)[-1]

    provider = SosResultsCohortProvider(session=session, source_id=sos_source.id)
    winners = (await provider.senate_winners()).get(odd_year, {})
    result = SenateCorroborationResult(odd_year=odd_year, winners=len(winners))
    if not winners:
        logger.info("senate_corroboration_no_winners", extra={"odd_year": odd_year})
        return result

    occupants = await open_senate_occupants(session)
    target = (await provider.citation_events()).get(odd_year)
    if target is not None:
        result.citations_added, result.mismatched_lds = await cite_elected_senators(
            session, winners, occupants, target=target, confidence=sos_source.reliability
        )
    result.missing_lds = missing_winner_lds(winners, occupants)
    _log(result)
    return result


def _log(result: SenateCorroborationResult) -> None:
    if result.ok:
        logger.info(
            "senate_corroboration_ok",
            extra={
                "odd_year": result.odd_year,
                "winners": result.winners,
                "citations_added": result.citations_added,
                "mismatched_lds": result.mismatched_lds,
            },
        )
        return
    logger.error(
        "senate_corroboration_violation",
        extra={
            "odd_year": result.odd_year,
            "winners": result.winners,
            "missing_lds": result.missing_lds,
            "mismatched_lds": result.mismatched_lds,
        },
    )


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Cite + corroborate the odd-year SOS Senate winners vs the open seats (#123)."
    )
    parser.add_argument(
        "--biennium",
        default=os.environ.get("USA_WA_BIENNIUM"),
        help="operating biennium (e.g. 2025-26); defaults to $USA_WA_BIENNIUM, else the "
        "date-current biennium. Consistent with the WSL/PDC/SOS refreshes' override",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="corroborate + build citations but roll back"
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            result = await corroborate_senate_winners(session, biennium=args.biennium)
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    finally:
        await engine.dispose()

    print(
        f"Senate corroboration (odd {result.odd_year}): winners={result.winners} "
        f"cited={result.citations_added} missing={result.missing_lds} "
        f"mismatched={result.mismatched_lds} "
        f"{'OK' if result.ok else 'VIOLATION'}"
        f"{' (dry-run, rolled back)' if args.dry_run else ''}"
    )
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
