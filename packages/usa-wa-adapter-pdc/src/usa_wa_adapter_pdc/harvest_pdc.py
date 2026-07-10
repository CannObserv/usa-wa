"""Phase A PDC harvester (#79) — archive historical winner cohorts (archive-only).

For each even election year in a range, archive the seated ``house-winners:<Y>`` +
``senate-winners:<Y>`` SODA cohorts through the runner's archive-only seam
(:meth:`~clearinghouse_core.runner.AdapterRunner.archive_only`) — pristine wire + #54 hash, no
normalize. Phase B (:mod:`build_pdc_spans`) derives the era-matched Position seats + identifiers
from this archive offline, because the derivation needs the seating biennium's roster the
harvest doesn't hold (the #75 fix).

Floor ~2008 (the PDC campaign-finance dataset's coverage); a year with no data simply archives
an empty cohort. Cohorts of a closed year are cache hits on re-run.

    python -m usa_wa_adapter_pdc.harvest_pdc --from-year 2008 [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.runner import AdapterRunner
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction
from usa_wa_adapter_legislature.refresh import biennium_for_date
from usa_wa_adapter_pdc.adapter import (
    HOUSE_WINNERS_RESOURCE_PREFIX,
    SENATE_WINNERS_RESOURCE_PREFIX,
    PDCAdapter,
    election_year_for_biennium,
)
from usa_wa_adapter_pdc.provisioning import get_or_create_source
from usa_wa_adapter_pdc.transport import PDCClient

logger = get_logger(__name__)

#: The PDC campaign-finance dataset's practical floor — earlier years archive empty cohorts.
DEFAULT_ELECTION_FLOOR = 2008


@dataclass(frozen=True)
class HarvestSummary:
    """Counts from one Phase A sweep."""

    years: int
    cohorts_archived: int
    dry_run: bool


def election_years(from_year: int, to_year: int) -> list[int]:
    """Inclusive even election years from ``from_year`` to ``to_year`` (an odd floor bumps up
    to the next even year — WA general elections that seat a legislature are even-year)."""
    start = from_year + (from_year % 2)
    return list(range(start, to_year + 1, 2))


async def harvest_pdc(
    session: AsyncSession,
    *,
    years: list[int],
    pdc_client: PDCClient | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> HarvestSummary:
    """Archive each year's House + Senate winner cohorts (archive-only). Operates in the
    caller's transaction (the CLI commits, or rolls back on ``dry_run``)."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    # archive_only never normalizes, so the adapter needs no rosters/anchors here.
    adapter = PDCAdapter(
        anchors=None,
        biennium=biennium_for_date(datetime.now(UTC).date()),
        house_roster={},
        client=pdc_client or PDCClient(app_token=os.environ.get("USA_WA_PDC_APP_TOKEN")),
        session=session,
    )
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
        for prefix in (HOUSE_WINNERS_RESOURCE_PREFIX, SENATE_WINNERS_RESOURCE_PREFIX):
            if await runner.archive_only(f"{prefix}{year}", force=force):
                archived += 1
        logger.info("pdc_cohort_year_harvested", extra={"year": year})

    return HarvestSummary(years=len(years), cohorts_archived=archived, dry_run=dry_run)


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Archive historical PDC winner cohorts (archive-only, #79 Phase A)."
    )
    parser.add_argument(
        "--from-year",
        type=int,
        default=DEFAULT_ELECTION_FLOOR,
        help=f"earliest election year (default {DEFAULT_ELECTION_FLOOR})",
    )
    parser.add_argument(
        "--to-year", type=int, default=None, help="default: the current election year"
    )
    parser.add_argument("--dry-run", action="store_true", help="harvest but roll back")
    parser.add_argument("--force", action="store_true", help="re-fetch past the freshness cache")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    to_year = args.to_year or election_year_for_biennium(
        biennium_for_date(datetime.now(UTC).date())
    )
    years = election_years(args.from_year, to_year)

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            summary = await harvest_pdc(
                session, years=years, dry_run=args.dry_run, force=args.force
            )
            if summary.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("pdc_harvest_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"PDC harvest: years={summary.years} cohorts_archived={summary.cohorts_archived} "
        f"{'(dry-run, rolled back)' if summary.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
