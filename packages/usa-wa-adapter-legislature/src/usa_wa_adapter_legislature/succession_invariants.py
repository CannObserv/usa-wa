"""Succession invariant checks (#107) — the anti-drift backstop + acceptance oracle.

An operator succession event (#107) is durable once entered, but a **missing** one is silent:
a member dies and nobody records it, so a ghost-open span inflates the chamber and the record
is wrong for up to a biennium. This oneshot makes that loud. It asserts, against the live open
seat cohort, two invariants:

- **Chamber-count** — open ``state_senator`` seats == 49, open ``state_representative`` == 98
  (147 total). High (50/99) ⇒ a ghost-open predecessor (a missing ``departed``/``vacated``);
  low (48/97) ⇒ an over-closed / unfilled seat (a missing ``seated``).
- **Duplicate-occupancy** — no single seat Role holds two open occupants, and no member holds
  two open seats in the same chamber (the "two open senators in LD5" shape directly).

    python -m usa_wa_adapter_legislature.succession_invariants

Read-only (app role, no writes); exits 0 clean / 1 on any violation (naming the offending
seats/members in the log) so the ``OnFailure=usa-wa-notify-failure@`` handler emails the
operator. Chamber sizes are current WA constants — a redistricting count change updates them.

**Historical audit mode (#119).** The daily gate probes the *open* cohort only, so a duplicate
occupancy that has since **closed** is invisible to it forever — sub-biennium sequential
occupancy collapsed onto the shared biennium floor (both occupants dated to the floor because
the wire can't date a mid-biennium handoff). ``--as-of DATE`` and ``--sweep-biennia`` re-run the
**duplicate-occupancy** check against a point-in-time snapshot (``valid_from <= D and (valid_to
is null or valid_to >= D)``) instead of ``is_active``, naming every offending (biennium, seat,
occupants) tuple:

    python -m usa_wa_adapter_legislature.succession_invariants --as-of 2009-01-01
    python -m usa_wa_adapter_legislature.succession_invariants --sweep-biennia

This is an **ad-hoc audit, not a timer** (a closed historical overlap is not actionable in the
"someone died and nobody told us *now*" sense the daily gate exists for); counts are
**reported, not gated** in history mode (House Position coverage floors at 2003-04, so pre-2003
biennia legitimately under-count) — the audit exits 0 unless ``--strict`` is given, which exits
1 on any duplicate (the post-backfill regression guard).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.identity import Assignment, Person, Role
from usa_wa_adapter_legislature.synthesis import biennium_for_date

logger = get_logger(__name__)

#: Current WA chamber sizes (49 LDs). A senator per LD; two representatives (Position 1/2) per LD.
SENATE_SEATS = 49
HOUSE_SEATS = 98

#: The earliest year ``--sweep-biennia`` probes — the WSL sponsor-archive Senate floor (#77).
SWEEP_FLOOR_YEAR = 1991

_SENATOR = "state_senator"
_REPRESENTATIVE = "state_representative"


@dataclass
class InvariantResult:
    """The invariant check outcome. ``ok`` is the exit gate (0 iff True)."""

    senate_open: int = 0
    house_open: int = 0
    expected_senate: int = SENATE_SEATS
    expected_house: int = HOUSE_SEATS
    duplicate_seats: list[tuple[str, int]] = field(default_factory=list)  # (role_id, occupants)
    duplicate_members: list[tuple[str, str, int]] = field(default_factory=list)  # (person, type, n)

    @property
    def count_ok(self) -> bool:
        return self.senate_open == self.expected_senate and self.house_open == self.expected_house

    @property
    def ok(self) -> bool:
        return self.count_ok and not self.duplicate_seats and not self.duplicate_members


@dataclass(frozen=True)
class SeatConflict:
    """One point-in-time duplicate occupancy (#119): a seat with >1 occupant at the probe date."""

    seat: str  # Role.source_id
    role_type: str
    occupants: list[str]  # occupant person names, sorted


def _seat_scope(stmt, as_of: date | None):
    """Restrict a seat-Assignment query to the occupants at the probe point.

    ``as_of is None`` — the live **open** cohort: ``is_active`` + both lifecycle tombstones NULL
    (the daily gate). ``as_of`` set — the **point-in-time** cohort: the validity window contains
    the date (a *closed* span still counts if it covers the date) + both tombstones NULL (the
    #119 historical audit). Never filters on ``is_active`` in as-of mode — a closed-but-covering
    span is exactly what the open cohort misses.
    """
    stmt = stmt.where(Assignment.deleted_at.is_(None), Assignment.archived_at.is_(None))
    if as_of is None:
        return stmt.where(Assignment.is_active.is_(True))
    return stmt.where(
        Assignment.valid_from <= as_of,
        or_(Assignment.valid_to.is_(None), Assignment.valid_to >= as_of),
    )


async def _seat_counts(session: AsyncSession, as_of: date | None) -> tuple[int, int]:
    """(senate, house) seat *occupancy* counts at the probe point — a count of occupying
    Assignments, so a doubly-occupied seat contributes 2 (that inflation is itself a signal)."""
    counts = dict(
        (
            await session.execute(
                _seat_scope(
                    select(Role.role_type, func.count())
                    .join(Assignment, Assignment.role_id == Role.id)
                    .where(Role.role_type.in_([_SENATOR, _REPRESENTATIVE])),
                    as_of,
                ).group_by(Role.role_type)
            )
        ).all()
    )
    return counts.get(_SENATOR, 0), counts.get(_REPRESENTATIVE, 0)


async def check_invariants(
    session: AsyncSession,
    *,
    expected_senate: int = SENATE_SEATS,
    expected_house: int = HOUSE_SEATS,
    as_of: date | None = None,
) -> InvariantResult:
    """Compute the seat counts + duplicate-occupancy violations (read-only).

    ``as_of`` selects the probe cohort — the live open cohort (None, the daily gate) or a
    point-in-time snapshot (a date, the #119 audit); see :func:`_seat_scope`.
    """
    senate, house = await _seat_counts(session, as_of)
    result = InvariantResult(
        senate_open=senate,
        house_open=house,
        expected_senate=expected_senate,
        expected_house=expected_house,
    )

    # Two occupants on one seat Role.
    dup_seats = (
        await session.execute(
            _seat_scope(
                select(Assignment.role_id, func.count())
                .join(Role, Assignment.role_id == Role.id)
                .where(Role.role_type.in_([_SENATOR, _REPRESENTATIVE])),
                as_of,
            )
            .group_by(Assignment.role_id)
            .having(func.count() > 1)
        )
    ).all()
    result.duplicate_seats = [(str(role_id), n) for role_id, n in dup_seats]

    # One member holding two seats in the same chamber.
    dup_members = (
        await session.execute(
            _seat_scope(
                select(Assignment.person_id, Role.role_type, func.count())
                .join(Role, Assignment.role_id == Role.id)
                .where(Role.role_type.in_([_SENATOR, _REPRESENTATIVE])),
                as_of,
            )
            .group_by(Assignment.person_id, Role.role_type)
            .having(func.count() > 1)
        )
    ).all()
    result.duplicate_members = [(str(pid), rtype, n) for pid, rtype, n in dup_members]
    return result


async def duplicate_occupancy_detail(session: AsyncSession, *, as_of: date) -> list[SeatConflict]:
    """Every seat with >1 occupant at ``as_of``, resolved to seat ``source_id`` + occupant
    names (#119) — the operator-actionable detail behind :attr:`InvariantResult.duplicate_seats`.

    One query joining ``Role`` + ``Person``; grouped in Python (seat cardinality is tiny). Names
    are sorted for a deterministic report.
    """
    rows = (
        await session.execute(
            _seat_scope(
                select(Role.source_id, Role.role_type, Person.name_full)
                .join(Assignment, Assignment.role_id == Role.id)
                .join(Person, Person.id == Assignment.person_id)
                .where(Role.role_type.in_([_SENATOR, _REPRESENTATIVE])),
                as_of,
            )
        )
    ).all()
    by_seat: dict[str, tuple[str, list[str]]] = {}
    for seat, role_type, name in rows:
        by_seat.setdefault(seat, (role_type, []))[1].append(name)
    return [
        SeatConflict(seat=seat, role_type=role_type, occupants=sorted(names))
        for seat, (role_type, names) in sorted(by_seat.items())
        if len(names) > 1
    ]


def _log(result: InvariantResult) -> None:
    if result.ok:
        logger.info(
            "succession_invariants_ok",
            extra={"senate_open": result.senate_open, "house_open": result.house_open},
        )
        return
    logger.error(
        "succession_invariants_violation",
        extra={
            "senate_open": result.senate_open,
            "expected_senate": result.expected_senate,
            "house_open": result.house_open,
            "expected_house": result.expected_house,
            "duplicate_seats": result.duplicate_seats,
            "duplicate_members": result.duplicate_members,
        },
    )


def sweep_years(from_year: int, to_year: int) -> list[int]:
    """Odd biennium start years from ``from_year`` up to and including ``to_year`` (biennia
    start on odd years, so an even ``from_year`` rolls back one)."""
    start = from_year if from_year % 2 == 1 else from_year - 1
    return list(range(start, to_year + 1, 2))


async def _run_audit(
    session: AsyncSession,
    *,
    probes: list[date],
) -> list[SeatConflict]:
    """The #119 point-in-time audit over ``probes`` — report per-probe occupancy counts + every
    duplicate; return the flat conflict list (across all probes) for the exit gate. Read-only.

    Counts are occupancy *rows* (``_seat_counts``), not distinct seats — a doubly-occupied seat
    inflates them (labelled ``*_occupants`` so this isn't misread as the seat total), and they
    are report-only here (no ``expected_*`` compare; House Position coverage floors at 2003-04).
    """
    all_conflicts: list[SeatConflict] = []
    for probe in probes:
        senate, house = await _seat_counts(session, probe)
        conflicts = await duplicate_occupancy_detail(session, as_of=probe)
        all_conflicts.extend(conflicts)
        label = biennium_for_date(probe)
        logger.info(
            "succession_audit_probe",
            extra={
                "biennium": label,
                "as_of": probe.isoformat(),
                "senate_occupants": senate,
                "house_occupants": house,
                "duplicate_seats": len(conflicts),
            },
        )
        print(
            f"{label} (as-of {probe.isoformat()}): "
            f"senate_occupants={senate} house_occupants={house} "
            f"dup_seats={len(conflicts)}"
        )
        for conflict in conflicts:
            print(f"    {conflict.seat} [{conflict.role_type}]: {' / '.join(conflict.occupants)}")
    return all_conflicts


def audit_exit_code(*, strict: bool, conflict_count: int) -> int:
    """The audit's exit contract (#119): 1 only when ``--strict`` and a duplicate was found (the
    post-backfill regression guard), else 0 — a historical duplicate is not a daily-gate
    failure. Pure, so the operator-facing exit contract is unit-testable without a live DB."""
    return 1 if (strict and conflict_count) else 0


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Assert the WA succession invariants (chamber counts + occupancy) (#107)."
    )
    parser.add_argument("--expected-senate", type=int, default=SENATE_SEATS)
    parser.add_argument("--expected-house", type=int, default=HOUSE_SEATS)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="YYYY-MM-DD: audit duplicate occupancy at a point in time (#119, report-only)",
    )
    parser.add_argument(
        "--sweep-biennia",
        action="store_true",
        help="audit duplicate occupancy at every biennium start (#119, report-only)",
    )
    parser.add_argument(
        "--from-year",
        type=int,
        default=SWEEP_FLOOR_YEAR,
        help=f"earliest year for --sweep-biennia (default {SWEEP_FLOOR_YEAR})",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="in audit mode, exit 1 if any duplicate occupancy is found (post-backfill guard)",
    )
    args = parser.parse_args(argv)

    if args.as_of is not None and args.sweep_biennia:
        print("--as-of and --sweep-biennia are mutually exclusive", file=sys.stderr)
        return 2

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    audit = args.as_of is not None or args.sweep_biennia
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            if audit:
                if args.sweep_biennia:
                    probes = [date(y, 1, 1) for y in sweep_years(args.from_year, date.today().year)]
                else:
                    probes = [args.as_of]
                conflicts = await _run_audit(session, probes=probes)
                print(f"Duplicate-occupancy audit: {len(conflicts)} conflict(s) across probes")
                return audit_exit_code(strict=args.strict, conflict_count=len(conflicts))
            result = await check_invariants(
                session,
                expected_senate=args.expected_senate,
                expected_house=args.expected_house,
            )
    finally:
        await engine.dispose()

    _log(result)
    print(
        f"Succession invariants: senate={result.senate_open}/{result.expected_senate} "
        f"house={result.house_open}/{result.expected_house} "
        f"dup_seats={len(result.duplicate_seats)} dup_members={len(result.duplicate_members)} "
        f"{'OK' if result.ok else 'VIOLATION'}"
    )
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
