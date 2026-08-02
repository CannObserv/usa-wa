"""House odd-year special-winner corroboration (#149) — the House sibling of #123 §2b.

``senate_corroboration.missing_winner_lds`` (#123 §2b) asserts that no odd-year **Senate** winner
lacks an open seat — a silent missing operator ``seated``. There was no House analog, so a House
odd-year **special** winner who never materializes into a ``state_representative`` Position seat was
caught by nothing. That is the LD30 Pos 2 2015-16 / Teri Hickel case: she won the Nov 2015 special,
the odd cohort was archived and named her, she was rostered — yet she sat *unseated* for months
because the backfill hadn't been run, and the daily refresh runs ``restrict_to_biennium=current``
(never re-emits a historical biennium). It was found by manual audit, not by an invariant. A
**unit** guard (#148) covers the odd-merge *code path* but cannot detect an *operational* gap (a
backfill that wasn't run). This closes that.

Two differences from the Senate check:

- **Keyed on ``(LD, position)``, not LD.** The House has two seats per LD (Position 1/2); the
  input is the odd-year ``house_winners()`` map (``{LD: [HousePosition]}``, winners-only — a
  *loser* candidacy must never false-match a seat), each carrying its ballot ``qualifier``.
- **Read-only, no citation half.** The House Position spans already cite the odd wire
  (``build.py``'s ``special_events``), so this needs only the 2b corroboration — no citation DML.

The gate keys on seat **existence**, not occupant identity — exactly as Senate §2b: a *wholly
unoccupied* winner seat is the unambiguous missing ``seated`` (exit 1); a seat held by *someone
other than the ballot winner* (a name change, or a predecessor not yet succeeded) is reported as
``mismatched`` but does **not** fail the gate (a surname divergence is more often a legitimate
ballot↔roster name change than a real missing succession).

    python -m usa_wa_adapter_sos.house_corroboration                  # daily current-biennium gate
    python -m usa_wa_adapter_sos.house_corroboration --sweep-biennia  # historical audit (report)

The daily gate probes the current biennium's odd cohort only (mirroring Senate).
``--sweep-biennia`` is the #119 pattern: a **report-only** point-in-time audit across every
archived odd year, for the LD30-as-history regression the daily gate can't reach; it exits 0 unless
``--strict`` (the post-backfill regression guard). House Position coverage floors at 2003-04, so
pre-coverage odd years legitimately under-report — reported, not gated.

Read-only (app role, no writes). Exit 0 clean / 1 on a missing winner / 2 on a config error.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from ulid import ULID as _ULID
from usa_wa_adapter_pdc.adapter import election_years_for_biennium
from usa_wa_adapter_pdc.normalize.positions import surname_match_set

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction
from usa_wa_adapter_legislature.synthesis import biennium_for_date
from usa_wa_adapter_sos.positions import HousePosition
from usa_wa_adapter_sos.provisioning import get_or_create_results_source
from usa_wa_adapter_sos.results.cohort import SosResultsCohortProvider

logger = get_logger(__name__)

#: The ``state_representative`` seat Role ``source_id`` prefix (``seat:house:ld-{n}:position-{p}``).
_HOUSE_SEAT_PREFIX = "seat:house:ld-"
_REPRESENTATIVE = "state_representative"

#: The earliest odd year ``--sweep-biennia`` probes — the WSL sponsor-archive floor (#77). House
#: Position coverage itself floors at 2003-04 (#118), so earlier odd years under-report (so the
#: sweep is report-only).
SWEEP_FLOOR_YEAR = 1991

#: A House seat key: ``(LD, "Position 1"|"Position 2")``.
HouseSeat = tuple[int, str]


@dataclass(frozen=True)
class HouseOccupant:
    """One occupant of a ``state_representative`` Position seat — the corroboration target."""

    assignment_id: _ULID
    name_full: str


@dataclass
class HouseCorroborationResult:
    """Outcome of one daily run. ``ok`` is the exit gate (0 iff no missing winner seat)."""

    odd_year: int
    winners: int = 0
    missing_seats: list[HouseSeat] = field(default_factory=list)
    mismatched_seats: list[HouseSeat] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_seats


@dataclass
class HouseSweepOutcome:
    """The ``--sweep-biennia`` audit result (#119 report-only). ``missing`` is ``(odd_year, LD,
    position)`` triples with no covering seat across the whole archive."""

    missing: list[tuple[int, int, str]] = field(default_factory=list)
    mismatched: list[tuple[int, int, str]] = field(default_factory=list)


def _seat_from_role_source_id(source_id: str) -> HouseSeat | None:
    """The ``(LD, qualifier)`` of a House seat Role ``source_id`` (``seat:house:ld-30:position-2``
    → ``(30, "Position 2")``), else ``None`` for any other role key (a title-keyed committee /
    leadership role, or a malformed key)."""
    if not source_id.startswith(_HOUSE_SEAT_PREFIX):
        return None
    tail = source_id[len(_HOUSE_SEAT_PREFIX) :]  # "30:position-2"
    parts = tail.split(":")
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    position_digit = parts[1].rsplit("-", 1)[-1]
    if position_digit not in {"1", "2"}:
        return None
    return int(parts[0]), f"Position {position_digit}"


def winner_seats(winners_by_ld: dict[int, list[HousePosition]]) -> dict[HouseSeat, HousePosition]:
    """Flatten the odd-year ``{LD: [HousePosition]}`` winners map to ``{(LD, qualifier):
    HousePosition}`` — one entry per ``(LD, position)`` race the special decided. Pure."""
    seats: dict[HouseSeat, HousePosition] = {}
    for ld, positions in winners_by_ld.items():
        for position in positions:
            seats[(ld, position.qualifier)] = position
    return seats


def missing_house_winner_seats(
    winners: dict[HouseSeat, HousePosition], occupants: dict[HouseSeat, list[HouseOccupant]]
) -> list[HouseSeat]:
    """The ``(LD, position)`` seats an odd-year House winner names with **no occupant** — a
    missing operator ``seated`` / an unrun backfill. Pure, so the gate is unit-testable offline."""
    return sorted(seat for seat in winners if not occupants.get(seat))


def mismatched_house_seats(
    winners: dict[HouseSeat, HousePosition], occupants: dict[HouseSeat, list[HouseOccupant]]
) -> list[HouseSeat]:
    """The occupied ``(LD, position)`` seats whose occupant's folded surname set does **not**
    intersect the ballot winner's — a stale/renamed seat, surfaced (not gated). Pure."""
    mismatched: list[HouseSeat] = []
    for seat, winner in winners.items():
        occ = occupants.get(seat) or []
        if occ and not any(surname_match_set(o.name_full) & winner.name_keys for o in occ):
            mismatched.append(seat)
    return sorted(mismatched)


def _seat_scope(stmt, as_of: date | None):
    """Restrict a seat-Assignment query to the probe cohort — the live **open** cohort
    (``as_of is None``: ``is_active`` + both tombstones NULL, the daily gate) or a **point-in-time**
    snapshot (``as_of`` set: the validity window contains the date, the #119 sweep). Mirrors
    ``succession_invariants._seat_scope``."""
    stmt = stmt.where(Assignment.deleted_at.is_(None), Assignment.archived_at.is_(None))
    if as_of is None:
        return stmt.where(Assignment.is_active.is_(True))
    return stmt.where(
        Assignment.valid_from <= as_of,
        or_(Assignment.valid_to.is_(None), Assignment.valid_to >= as_of),
    )


async def house_occupants(
    session: AsyncSession, *, as_of: date | None = None
) -> dict[HouseSeat, list[HouseOccupant]]:
    """``{(LD, qualifier): [HouseOccupant]}`` for every ``state_representative`` Position seat at
    the probe point (open cohort, or point-in-time when ``as_of`` is set — see :func:`_seat_scope`).

    Keyed on the seat Role ``source_id``; a role with any other key is ignored. A doubly-occupied
    seat yields a two-element list (a distinct violation the succession invariants assert)."""
    rows = (
        await session.execute(
            _seat_scope(
                select(Role.source_id, Assignment.id, Person.name_full)
                .join(Assignment, Assignment.role_id == Role.id)
                .join(Person, Person.id == Assignment.person_id)
                .where(Role.role_type == _REPRESENTATIVE),
                as_of,
            )
        )
    ).all()
    by_seat: dict[HouseSeat, list[HouseOccupant]] = {}
    for source_id, assignment_id, name_full in rows:
        seat = _seat_from_role_source_id(source_id)
        if seat is None:
            continue
        by_seat.setdefault(seat, []).append(
            HouseOccupant(assignment_id=assignment_id, name_full=name_full)
        )
    return by_seat


async def corroborate_house_winners(
    session: AsyncSession, *, biennium: str | None = None
) -> HouseCorroborationResult:
    """Corroborate the current biennium's **odd-year** House special winners vs the open seats.

    The odd November is the mid-biennium special seating a representative the daily refresh won't
    re-emit for a historical biennium; the even seating year's winners are already dated by the
    sponsor roster, so this consumes only the odd cohort (``election_years_for_biennium(...)[-1]``).
    An unarchived / not-yet-held odd cohort is an empty winner set → a clean no-op (0 winners, ok).
    """
    jurisdiction = await resolve_jurisdiction(session)
    sos_source = await get_or_create_results_source(session, jurisdiction)
    date_current = biennium_for_date(datetime.now(UTC).date())
    current = biennium or date_current
    if current != date_current:
        # A stale $USA_WA_BIENNIUM / --biennium pin would silently gate the wrong biennium's
        # winners with no breadcrumb — mirror the WSL/PDC/SOS refreshes + Senate corroboration.
        logger.warning(
            "house_corroboration_noncurrent_biennium",
            extra={"biennium": current, "date_current": date_current},
        )
    odd_year = election_years_for_biennium(current)[-1]

    provider = SosResultsCohortProvider(session=session, source_id=sos_source.id)
    winners = winner_seats((await provider.house_winners()).get(odd_year, {}))
    result = HouseCorroborationResult(odd_year=odd_year, winners=len(winners))
    if not winners:
        logger.info("house_corroboration_no_winners", extra={"odd_year": odd_year})
        return result

    occupants = await house_occupants(session)
    result.mismatched_seats = mismatched_house_seats(winners, occupants)
    result.missing_seats = missing_house_winner_seats(winners, occupants)
    _log(result)
    return result


async def sweep_house_winners(
    session: AsyncSession, *, from_year: int = SWEEP_FLOOR_YEAR
) -> HouseSweepOutcome:
    """Point-in-time audit (#119, report-only) of **every archived odd year's** House winners vs
    the seats that covered them — the historical regression the daily current-biennium gate can't
    reach (the LD30-2015-16 shape). Each winner ``(LD, position)`` is probed against the occupancy
    on the odd year's election date; a seat with no covering occupant is ``missing``, a covering
    occupant not matching the ballot name is ``mismatched`` (surfaced, never gated here)."""
    jurisdiction = await resolve_jurisdiction(session)
    sos_source = await get_or_create_results_source(session, jurisdiction)
    provider = SosResultsCohortProvider(session=session, source_id=sos_source.id)
    house_winners = await provider.house_winners()

    outcome = HouseSweepOutcome()
    for odd_year in sorted(y for y in house_winners if y % 2 == 1 and y >= from_year):
        winners = winner_seats(house_winners.get(odd_year, {}))
        if not winners:
            continue
        as_of = date(odd_year, 12, 31)  # after the November special, within the seating biennium
        occupants = await house_occupants(session, as_of=as_of)
        for ld, position in missing_house_winner_seats(winners, occupants):
            outcome.missing.append((odd_year, ld, position))
        for ld, position in mismatched_house_seats(winners, occupants):
            outcome.mismatched.append((odd_year, ld, position))
        logger.info(
            "house_corroboration_sweep_probe",
            extra={"odd_year": odd_year, "winners": len(winners)},
        )
    return outcome


def _log(result: HouseCorroborationResult) -> None:
    if result.ok:
        logger.info(
            "house_corroboration_ok",
            extra={
                "odd_year": result.odd_year,
                "winners": result.winners,
                "mismatched_seats": result.mismatched_seats,
            },
        )
        return
    logger.error(
        "house_corroboration_violation",
        extra={
            "odd_year": result.odd_year,
            "winners": result.winners,
            "missing_seats": result.missing_seats,
            "mismatched_seats": result.mismatched_seats,
        },
    )


def sweep_exit_code(*, strict: bool, missing_count: int) -> int:
    """The sweep's exit contract (#119): 1 only when ``--strict`` and a missing seat was found (the
    post-backfill regression guard), else 0 — a historical gap is not a daily-gate failure (House
    Position coverage floors at 2003-04). Pure, so the exit contract is unit-testable."""
    return 1 if (strict and missing_count) else 0


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Corroborate odd-year SOS House special winners vs the open seats (#149)."
    )
    parser.add_argument(
        "--biennium",
        default=os.environ.get("USA_WA_BIENNIUM"),
        help="operating biennium (e.g. 2025-26); defaults to $USA_WA_BIENNIUM, else the "
        "date-current biennium. Consistent with the WSL/PDC/SOS refreshes' override",
    )
    parser.add_argument(
        "--sweep-biennia",
        action="store_true",
        help="audit every archived odd year vs point-in-time occupancy (#119, report-only)",
    )
    parser.add_argument(
        "--from-year",
        type=int,
        default=SWEEP_FLOOR_YEAR,
        help=f"earliest odd year for --sweep-biennia (default {SWEEP_FLOOR_YEAR})",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="in sweep mode, exit 1 if any missing seat is found (post-backfill guard)",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            if args.sweep_biennia:
                outcome = await sweep_house_winners(session, from_year=args.from_year)
                print(
                    f"House sweep: {len(outcome.missing)} missing seat(s), "
                    f"{len(outcome.mismatched)} mismatched across odd years"
                )
                for odd_year, ld, position in outcome.missing:
                    print(f"    MISSING {odd_year}: LD{ld} {position}")
                for odd_year, ld, position in outcome.mismatched:
                    print(f"    mismatched {odd_year}: LD{ld} {position}")
                return sweep_exit_code(strict=args.strict, missing_count=len(outcome.missing))
            result = await corroborate_house_winners(session, biennium=args.biennium)
    finally:
        await engine.dispose()

    print(
        f"House corroboration (odd {result.odd_year}): winners={result.winners} "
        f"missing={result.missing_seats} mismatched={result.mismatched_seats} "
        f"{'OK' if result.ok else 'VIOLATION'}"
    )
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
