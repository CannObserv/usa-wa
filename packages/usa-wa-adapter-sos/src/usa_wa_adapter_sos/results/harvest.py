"""Phase A results harvester (#101) — archive ``results.vote.wa.gov`` legislative cohorts.

For each even general-election year in a range, archive the ``sos-legresults:<YYYYMMDD>`` CSV
cohort through :meth:`~clearinghouse_core.runner.AdapterRunner.archive_only` — pristine wire +
#54 hash, no normalize. Phase B (:mod:`usa_wa_adapter_sos.results.cohort`) derives the House
Position from this archive offline.

**Per-year resilient.** A year the source 404s/500s (an unheld year, an outage) or that carries no
Legislative CSV (:class:`LegislativeExportNotFound`) is **skipped-and-logged** inside its own
SAVEPOINT, and the years the sweep *reached* still commit — unlike an all-or-nothing sweep that
rolls the whole run back on one bad year. This source genuinely needs it: filenames vary per year
(discovered via ``export.html``) and future years 404 until held.

Floor **2008** (the PDC winner floor + the earliest results this fills against).

    python -m usa_wa_adapter_sos.results.harvest --from-year 2008 --to-year 2024 [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from usa_wa_adapter_pdc.adapter import election_year_for_biennium

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.runner import AdapterRunner
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction
from usa_wa_adapter_legislature.refresh import biennium_for_date
from usa_wa_adapter_sos.provisioning import get_or_create_results_source
from usa_wa_adapter_sos.results.adapter import ResultsAdapter, legresults_resource_id
from usa_wa_adapter_sos.results.transport import (
    LegislativeExportNotFound,
    SOSResultsClient,
    configure_results_rate_limit,
)

logger = get_logger(__name__)

#: The earliest general-election year this source fills against (the PDC winner floor).
DEFAULT_ELECTION_FLOOR = 2008


@dataclass(frozen=True)
class HarvestSummary:
    """Counts from one Phase A sweep."""

    years: int
    cohorts_archived: int
    cohorts_skipped: int
    dry_run: bool


def general_election_years(from_year: int, to_year: int) -> list[int]:
    """Inclusive even general-election years from ``from_year`` to ``to_year`` (an odd floor bumps
    up to the next even year — WA legislative elections are even)."""
    start = from_year + (from_year % 2)
    return list(range(start, to_year + 1, 2))


async def harvest_results(
    session: AsyncSession,
    *,
    years: list[int],
    results_client: SOSResultsClient | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> HarvestSummary:
    """Archive each year's legislative-results cohort (archive-only), **per-year resilient**.

    Each year runs in its own SAVEPOINT: a source HTTP error or a missing Legislative CSV rolls
    back *that year* and is counted ``cohorts_skipped``; the reached years persist. Operates in
    the caller's transaction (the CLI commits, or rolls back on ``dry_run``)."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_results_source(session, jurisdiction)
    adapter = ResultsAdapter(election_years=years, client=results_client or SOSResultsClient())
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        fill_only=True,
    )

    archived = skipped = 0
    for year in years:
        try:
            async with session.begin_nested():
                if await runner.archive_only(legresults_resource_id(year), force=force):
                    archived += 1
            logger.info("results_cohort_year_harvested", extra={"year": year})
        except (httpx.HTTPStatusError, LegislativeExportNotFound) as exc:
            skipped += 1
            logger.warning("results_cohort_year_skipped", extra={"year": year, "error": str(exc)})

    return HarvestSummary(
        years=len(years), cohorts_archived=archived, cohorts_skipped=skipped, dry_run=dry_run
    )


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Archive results.vote.wa.gov legislative cohorts (archive-only, #101 Phase A)."
    )
    parser.add_argument(
        "--from-year",
        type=int,
        default=DEFAULT_ELECTION_FLOOR,
        help=f"earliest general-election year (default {DEFAULT_ELECTION_FLOOR})",
    )
    parser.add_argument(
        "--to-year", type=int, default=None, help="default: the current seating election year"
    )
    parser.add_argument("--dry-run", action="store_true", help="harvest but roll back")
    parser.add_argument("--force", action="store_true", help="re-fetch past the freshness cache")
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=1.0,
        help="central results.vote.wa.gov min-interval between calls (courtesy; default 1.0)",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    configure_results_rate_limit(args.pause_seconds)
    to_year = args.to_year or election_year_for_biennium(
        biennium_for_date(datetime.now(UTC).date())
    )
    years = general_election_years(args.from_year, to_year)

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            summary = await harvest_results(
                session, years=years, dry_run=args.dry_run, force=args.force
            )
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("results_harvest_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"Results harvest: years={summary.years} archived={summary.cohorts_archived} "
        f"skipped={summary.cohorts_skipped} "
        f"{'(dry-run, rolled back)' if summary.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
