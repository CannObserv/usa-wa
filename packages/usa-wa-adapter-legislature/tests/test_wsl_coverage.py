"""WSL source coverage (#180) — the declared claims, and the constants derived from them."""

from __future__ import annotations

from sqlalchemy import select

from clearinghouse_core.source_coverage import CoverageStatus, SourceCoverage
from usa_wa_adapter_legislature.coverage import (
    COMMITTEE_MEMBERSHIP,
    COMMITTEE_MEMBERSHIP_COVERAGE,
    SPONSOR_ROSTER,
    SPONSOR_ROSTER_COVERAGE,
    WSL_COVERAGE,
)
from usa_wa_adapter_legislature.membership.harvest import DEFAULT_MEMBERSHIP_FLOOR
from usa_wa_adapter_legislature.operators.invariants import SWEEP_FLOOR_YEAR
from usa_wa_adapter_legislature.provisioning import get_or_create_source
from usa_wa_adapter_legislature.sponsors.probe_identity import DEFAULT_HISTORY_FLOOR


def test_one_wsl_source_publishes_two_dimensions_with_different_floors():
    """WSL serves ``GetSponsors`` from 1991-92 but ``GetCommitteeMembers`` only from 1999-00.
    A single per-source floor cannot say that; the ``dimension`` axis is what makes the two
    separately answerable instead of separately hardcoded."""
    assert SPONSOR_ROSTER_COVERAGE.range_start == "1991-92"
    assert COMMITTEE_MEMBERSHIP_COVERAGE.range_start == "1999-00"
    assert {c.dimension for c in WSL_COVERAGE} == {SPONSOR_ROSTER, COMMITTEE_MEMBERSHIP}


def test_the_membership_floor_is_declared_assumed_not_verified():
    """The audit's honest output: the sponsor floor carries a dated probe (2026-07-08, 1989-90
    faults), the membership floor is a "~1999-00" nobody dated. ``assumed`` says so rather than
    letting an unchecked bound pass as a checked one — which is the whole point of recording a
    status alongside the range."""
    assert SPONSOR_ROSTER_COVERAGE.status == CoverageStatus.verified
    assert COMMITTEE_MEMBERSHIP_COVERAGE.status == CoverageStatus.assumed


def test_the_cli_floors_are_the_claims():
    """The floors were three independent declarations across two packages. They are now one
    claim, projected: a biennium label for the biennium-keyed sweeps, its leading year for the
    year-keyed ones. Derived in pure Python so ``--help`` still works with no database."""
    assert DEFAULT_HISTORY_FLOOR == SPONSOR_ROSTER_COVERAGE.range_start
    assert DEFAULT_MEMBERSHIP_FLOOR == COMMITTEE_MEMBERSHIP_COVERAGE.range_start
    assert SWEEP_FLOOR_YEAR == SPONSOR_ROSTER_COVERAGE.floor_year == 1991


async def test_provisioning_seeds_the_claims(db_session, usa_wa):
    """Coverage rows exist as soon as the Source does — the ARCHITECTURE.md checklist step
    ("coverage rows must exist before an application builds on the feed") holds by construction
    rather than by remembering to run something."""
    source = await get_or_create_source(db_session, usa_wa)
    rows = (
        (
            await db_session.execute(
                select(SourceCoverage).where(SourceCoverage.source_id == source.id)
            )
        )
        .scalars()
        .all()
    )
    assert {(r.dimension, r.range_start, r.status) for r in rows} == {
        (c.dimension, c.range_start, c.status.value) for c in WSL_COVERAGE
    }


async def test_provisioning_seeds_on_an_existing_source_row(db_session, usa_wa):
    """A deployment whose Source rows predate #180 must still get its coverage — so the seed
    runs on the get path, not only the create path."""
    source = await get_or_create_source(db_session, usa_wa)
    await db_session.execute(
        SourceCoverage.__table__.delete().where(SourceCoverage.source_id == source.id)
    )
    await get_or_create_source(db_session, usa_wa)
    rows = (
        (
            await db_session.execute(
                select(SourceCoverage).where(SourceCoverage.source_id == source.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == len(WSL_COVERAGE)
