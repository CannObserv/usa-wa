"""Phase A SOS harvester (#100) — archive historical votewa filing cohorts (archive-only).

For each even general-election year in a range, archive the ``sos-whofiled:<YYYYMM>`` CSV cohort
through the runner's archive-only seam (``AdapterRunner.archive_only``) — pristine wire + #54
hash, no normalize. Phase B (:mod:`build_house_spans`) derives the House ``Position`` from this
archive offline (the WSL+SOS House Position seat, #101).

Floor **2008** — the PDC winner floor this fills against; earlier years have no PDC cohort to
join. Cohorts of a closed year are cache hits on re-run.

    python -m usa_wa_adapter_sos.harvest_sos --from-year 2008 --to-year 2016 [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from usa_wa_adapter_pdc.adapter import election_year_for_biennium

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.runner import AdapterRunner
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction
from usa_wa_adapter_legislature.refresh import biennium_for_date
from usa_wa_adapter_sos.adapter import SOSAdapter, whofiled_resource_id
from usa_wa_adapter_sos.provisioning import get_or_create_source
from usa_wa_adapter_sos.transport import SOSClient, configure_sos_rate_limit

logger = get_logger(__name__)

#: The PDC winner floor this backfill fills against — earlier years have no PDC cohort to join.
DEFAULT_ELECTION_FLOOR = 2008


@dataclass(frozen=True)
class HarvestSummary:
    """Counts from one Phase A sweep."""

    years: int
    cohorts_archived: int
    dry_run: bool


def general_election_years(from_year: int, to_year: int) -> list[int]:
    """Inclusive even general-election years from ``from_year`` to ``to_year`` (an odd floor
    bumps up to the next even year — WA general elections that seat a legislature are even)."""
    start = from_year + (from_year % 2)
    return list(range(start, to_year + 1, 2))


async def harvest_sos(
    session: AsyncSession,
    *,
    years: list[int],
    sos_client: SOSClient | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> HarvestSummary:
    """Archive each year's filing cohort (archive-only). Operates in the caller's transaction
    (the CLI commits, or rolls back on ``dry_run``). A mid-sweep failure aborts the whole run;
    re-run from the floor — closed years cache-hit, so it resumes cheaply."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    adapter = SOSAdapter(election_years=years, client=sos_client or SOSClient())
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        fill_only=True,
    )

    archived = 0
    for year in years:
        if await runner.archive_only(whofiled_resource_id(year), force=force):
            archived += 1
        logger.info("sos_cohort_year_harvested", extra={"year": year})

    return HarvestSummary(years=len(years), cohorts_archived=archived, dry_run=dry_run)


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Archive historical votewa filing cohorts (archive-only, #100 Phase A)."
    )
    parser.add_argument(
        "--from-year",
        type=int,
        default=DEFAULT_ELECTION_FLOOR,
        help=f"earliest general-election year (default {DEFAULT_ELECTION_FLOOR})",
    )
    parser.add_argument(
        "--to-year", type=int, default=None, help="default: the current general-election year"
    )
    parser.add_argument("--dry-run", action="store_true", help="harvest but roll back")
    parser.add_argument("--force", action="store_true", help="re-fetch past the freshness cache")
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=1.0,
        help="central votewa min-interval between calls (courtesy floor; default 1.0)",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    configure_sos_rate_limit(args.pause_seconds)
    to_year = args.to_year or election_year_for_biennium(
        biennium_for_date(datetime.now(UTC).date())
    )
    years = general_election_years(args.from_year, to_year)

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            summary = await harvest_sos(
                session, years=years, dry_run=args.dry_run, force=args.force
            )
            if summary.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("sos_harvest_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"SOS harvest: years={summary.years} cohorts_archived={summary.cohorts_archived} "
        f"{'(dry-run, rolled back)' if summary.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
