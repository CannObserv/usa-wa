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
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.identity import Assignment, Role

logger = get_logger(__name__)

#: Current WA chamber sizes (49 LDs). A senator per LD; two representatives (Position 1/2) per LD.
SENATE_SEATS = 49
HOUSE_SEATS = 98

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


def _open_seat_where(stmt):
    """Restrict to live, open seat Assignments (both lifecycle tombstones NULL)."""
    return stmt.where(
        Assignment.is_active.is_(True),
        Assignment.deleted_at.is_(None),
        Assignment.archived_at.is_(None),
    )


async def check_invariants(
    session: AsyncSession,
    *,
    expected_senate: int = SENATE_SEATS,
    expected_house: int = HOUSE_SEATS,
) -> InvariantResult:
    """Compute the open-seat counts + duplicate-occupancy violations (read-only)."""
    counts = dict(
        (
            await session.execute(
                _open_seat_where(
                    select(Role.role_type, func.count())
                    .join(Assignment, Assignment.role_id == Role.id)
                    .where(Role.role_type.in_([_SENATOR, _REPRESENTATIVE]))
                ).group_by(Role.role_type)
            )
        ).all()
    )
    result = InvariantResult(
        senate_open=counts.get(_SENATOR, 0),
        house_open=counts.get(_REPRESENTATIVE, 0),
        expected_senate=expected_senate,
        expected_house=expected_house,
    )

    # Two open occupants on one seat Role.
    dup_seats = (
        await session.execute(
            _open_seat_where(
                select(Assignment.role_id, func.count())
                .join(Role, Assignment.role_id == Role.id)
                .where(Role.role_type.in_([_SENATOR, _REPRESENTATIVE]))
            )
            .group_by(Assignment.role_id)
            .having(func.count() > 1)
        )
    ).all()
    result.duplicate_seats = [(str(role_id), n) for role_id, n in dup_seats]

    # One member holding two open seats in the same chamber.
    dup_members = (
        await session.execute(
            _open_seat_where(
                select(Assignment.person_id, Role.role_type, func.count())
                .join(Role, Assignment.role_id == Role.id)
                .where(Role.role_type.in_([_SENATOR, _REPRESENTATIVE]))
            )
            .group_by(Assignment.person_id, Role.role_type)
            .having(func.count() > 1)
        )
    ).all()
    result.duplicate_members = [(str(pid), rtype, n) for pid, rtype, n in dup_members]
    return result


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


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Assert the WA succession invariants (chamber counts + occupancy) (#107)."
    )
    parser.add_argument("--expected-senate", type=int, default=SENATE_SEATS)
    parser.add_argument("--expected-house", type=int, default=HOUSE_SEATS)
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
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
