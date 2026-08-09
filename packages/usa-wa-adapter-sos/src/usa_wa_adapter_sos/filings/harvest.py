"""Phase A SOS harvester (#100) — archive historical votewa filing cohorts (archive-only).

For each even general-election year in a range, archive the ``sos-whofiled:<YYYYMM>`` CSV cohort
through the runner's archive-only seam (``AdapterRunner.archive_only``) — pristine wire + #54
hash, no normalize. Phase B (:mod:`build_house_spans`) derives the House ``Position`` from this
archive offline (the WSL+SOS House Position seat, #101).

Floor **2008** — the PDC winner floor this fills against; earlier years have no PDC cohort to
join. Ceiling **2018** — votewa retired the ``ExportToExcel`` export to Power BI after the 2018
general, so this is a closed archive (see :data:`DEFAULT_ELECTION_CEILING`). Cohorts of a closed
year are cache hits on re-run.

**Per-year resilient (#169).** A year the source can't serve is skipped-and-logged inside its own
SAVEPOINT and the years the sweep *reached* still commit, rather than one bad year discarding the
whole run. That matters past the 1-day ``cache_ttl_days``, where an aborted sweep's re-run re-pulls
every year against a low-QPS government host — exactly the traffic the courtesy limiter exists to
avoid.

    python -m usa_wa_adapter_sos.filings.harvest --from-year 2008 --to-year 2016 [--dry-run]
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

from clearinghouse_core.job import EXIT_CONFIG as _EXIT_CONFIG
from clearinghouse_core.job import EXIT_DEGRADED as _EXIT_DEGRADED
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.runner import AdapterRunner
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction
from usa_wa_adapter_sos.coverage import SOS_FILINGS_ELECTION_YEARS
from usa_wa_adapter_sos.filings.adapter import SOSAdapter, whofiled_resource_id
from usa_wa_adapter_sos.filings.transport import SOSFilingsClient, configure_sos_rate_limit
from usa_wa_adapter_sos.provisioning import get_or_create_source
from usa_wa_common.elections import election_year_for_biennium

logger = get_logger(__name__)

#: The PDC winner floor this backfill fills against — earlier years have no PDC cohort to join.
#: Both bounds come from the declared coverage claim (#180), which also carries the ``absent``
#: 2020-onward claim naming the gap this ceiling exists to stop short of.
DEFAULT_ELECTION_FLOOR = SOS_FILINGS_ELECTION_YEARS.floor_year

#: Exit code for a whole-source outage (#169 / CR #196 finding 22). Imported from the shared job
#: harness rather than re-declared so this CLI's exit contract is already correct when #179b
#: migrates it onto ``run_job()``; systemd's ``OnFailure=`` fires on it like any other failure.
EXIT_DEGRADED = _EXIT_DEGRADED

#: Exit code for an operator config error — an empty year range (CR #196 finding 26).
EXIT_CONFIG = _EXIT_CONFIG

#: The last general this source serves. SOS retired the ``WhoFiled`` *Export To Excel* control to
#: Power BI after the 2018 general; ``electionDate=202011`` and later return HTTP 500, permanently
#: (verified live 2026-08-06, consistent with the 2026-07-18 audit that moved the House Position
#: seat onto the results source at #101). This is a **closed archive**, not a feed waiting to come
#: back, so the wall-clock default is capped here — otherwise the bare CLI invocation sweeps into
#: years guaranteed to fail, as it has since 2020.
#:
#: Deliberately caps the *computed* default only: an explicit ``--to-year`` is an operator
#: assertion (a probe of whether votewa ever restores the export) and stays honoured. Per-year
#: resilience is what makes a wrong explicit bound survivable rather than fatal — the ceiling and
#: the SAVEPOINT are complementary, neither substitutes for the other (#169).
DEFAULT_ELECTION_CEILING = SOS_FILINGS_ELECTION_YEARS.ceiling_year
if DEFAULT_ELECTION_CEILING is None:  # pragma: no cover — the claim is a closed archive
    # ``ceiling_year`` is ``int | None`` and ``min(computed, None)`` is a TypeError, so state the
    # closed-archive dependency here rather than crashing mid-sweep (CR #196 finding 29). If the
    # claim ever legitimately re-opens, the fix is to drop the cap, not to widen this.
    raise RuntimeError(
        "the filings coverage claim has no ceiling; --to-year capping assumes a closed archive"
    )


@dataclass(frozen=True)
class HarvestSummary:
    """Counts from one Phase A sweep.

    ``cohorts_skipped`` counts years the source could not serve. Unlike the results source there
    is no absent/skipped split: filings has no per-year filename discovery and so no
    ``LegislativeExportNotFound`` analogue — every failure here is an HTTP failure.
    """

    years: int
    cohorts_archived: int
    cohorts_skipped: int
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
    sos_client: SOSFilingsClient | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> HarvestSummary:
    """Archive each year's filing cohort (archive-only), **per-year resilient** (#169).

    Each year runs in its own SAVEPOINT: any source-side HTTP failure — a status error (2020+ 500s
    since the Power BI retirement) or a transport error (connect/read timeout, reset) — rolls back
    *that year* and the reached years persist. Operates in the caller's transaction (the CLI
    commits, or rolls back on ``dry_run``).
    """
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    adapter = SOSAdapter(election_years=years, client=sos_client or SOSFilingsClient())
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
                if await runner.archive_only(whofiled_resource_id(year), force=force):
                    archived += 1
            logger.info("sos_cohort_year_harvested", extra={"year": year})
        except httpx.HTTPError as exc:
            # httpx.HTTPError is the common base of HTTPStatusError (4xx/5xx) and TransportError
            # (timeouts/connect resets): both mean the source couldn't serve this year, so skip
            # the year not the sweep. A DB/SQLAlchemy error is not an httpx error, so it aborts.
            skipped += 1
            logger.warning("sos_cohort_year_skipped", extra={"year": year, "error": str(exc)})

    if skipped > 0 and skipped == len(years):
        # *Every* year failed — a whole-source outage, not one bad year in a good run.
        #
        # The test is "all years skipped", not "archived == 0" (CR #196 finding 23):
        # ``archive_only`` returns False on a **cache hit**, so ``archived`` counts fetches, not
        # successes. A sweep where four years cache-hit and one fails has archived == 0 while the
        # source served four of five — firing the alarm loudest on the normal re-run path is how
        # alarms get ignored.
        logger.warning("sos_harvest_total_outage", extra={"years": len(years), "skipped": skipped})

    return HarvestSummary(
        years=len(years), cohorts_archived=archived, cohorts_skipped=skipped, dry_run=dry_run
    )


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
        "--to-year",
        type=int,
        default=None,
        help=(
            "latest general-election year (default: the current general-election year, capped at "
            f"{DEFAULT_ELECTION_CEILING} — the last general this source serves)"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="harvest but roll back")
    parser.add_argument("--force", action="store_true", help="re-fetch past the freshness cache")
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=None,
        help=(
            "central votewa min-interval between calls (courtesy floor); unset leaves the value "
            "seeded from USA_WA_SOS_MIN_REQUEST_INTERVAL (default 1.0) in place"
        ),
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    # Only override the central limiter when the operator actually asked (#169): an unconditional
    # call let the flag's own default overwrite the env-seeded interval, making
    # USA_WA_SOS_MIN_REQUEST_INTERVAL dead config — this CLI is its only production caller.
    if args.pause_seconds is not None:
        configure_sos_rate_limit(args.pause_seconds)
    # Cap the wall-clock default at the ceiling; an explicit --to-year is honoured as given.
    to_year = args.to_year or min(
        election_year_for_biennium(biennium_for_date(datetime.now(UTC).date())),
        DEFAULT_ELECTION_CEILING,
    )
    years = general_election_years(args.from_year, to_year)
    if not years:
        # An empty range is an operator error, not a no-op success (CR #196 finding 26): the
        # ceiling makes ``--from-year 2020`` select nothing, and printing "years=0 (committed)"
        # with exit 0 hides exactly the years the ceiling exists to talk about.
        print(
            f"no general-election years in [{args.from_year}, {to_year}] — this source serves "
            f"{DEFAULT_ELECTION_FLOOR}-{DEFAULT_ELECTION_CEILING}; aborting",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            summary = await harvest_sos(
                session, years=years, dry_run=args.dry_run, force=args.force
            )
            if args.dry_run:
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
        f"cohorts_skipped={summary.cohorts_skipped} "
        f"{'(dry-run, rolled back)' if summary.dry_run else '(committed)'}"
    )
    if summary.cohorts_skipped > 0 and summary.cohorts_skipped == summary.years:
        # Every year failed. Per-year resilience means no year crashed the sweep, so without this
        # the run exits 0 and the whole-source outage is a WARNING nobody consumes — the exact
        # gap ``clearinghouse_core.runs`` was built to close (#178), which names this harvest's
        # sibling in its own module docstring. #169 required it: "or the 2020+ case degrades from
        # a loud exit 1 to a silent exit 0" (CR #196 finding 22).
        return EXIT_DEGRADED
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
