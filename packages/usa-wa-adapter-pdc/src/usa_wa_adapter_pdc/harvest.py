"""Phase A PDC harvester (#79) — archive historical winner cohorts (archive-only).

For **every** general-election year in a range — even seating years AND odd special years
(#121; WA holds a general each November, and odd-year specials seat legislators) — archive the
seated ``house-winners:<Y>`` + ``senate-winners:<Y>`` SODA cohorts through the runner's
archive-only seam (:meth:`~clearinghouse_core.runner.AdapterRunner.archive_only`) — pristine
wire + #54 hash, no normalize. Phase B (:mod:`build_pdc_spans`) derives the era-matched
identifier links from this archive offline, because the derivation needs the seating biennium's
roster the harvest doesn't hold (the #75 fix).

Floor ~2008 (the PDC campaign-finance dataset's coverage); a year with no data simply archives
an empty cohort (negative evidence — no error path, unlike the SOS results source). Cohorts of
a closed year are cache hits on re-run.

    python -m usa_wa_adapter_pdc.harvest --from-year 2008 [--dry-run]
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
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_pdc.adapter import (
    HOUSE_WINNERS_RESOURCE_PREFIX,
    SENATE_WINNERS_RESOURCE_PREFIX,
    PDCAdapter,
)
from usa_wa_adapter_pdc.coverage import PDC_ELECTION_YEARS
from usa_wa_adapter_pdc.provisioning import get_or_create_source
from usa_wa_adapter_pdc.transport import PDCClient
from usa_wa_common.jurisdiction import resolve_jurisdiction

logger = get_logger(__name__)

#: The PDC campaign-finance dataset's practical floor — earlier years archive empty cohorts. The
#: declared coverage claim (#180), which records it as **assumed**: an under-served year archives
#: an empty cohort rather than failing, so a wrong floor here is invisible.
DEFAULT_ELECTION_FLOOR = PDC_ELECTION_YEARS.floor_year


@dataclass(frozen=True)
class HarvestSummary:
    """Counts from one Phase A sweep."""

    years: int
    cohorts_archived: int
    dry_run: bool


def election_years(from_year: int, to_year: int) -> list[int]:
    """Inclusive general-election years from ``from_year`` to ``to_year`` — **every** year, not
    just even ones (#121): WA holds a general each November, and odd-year specials seat
    legislators (Nov 2025: Hunt/Krishnadasan/Zahn). A year with no legislative race archives an
    empty SODA cohort — cheap negative evidence, no error path."""
    return list(range(from_year, to_year + 1))


async def harvest_pdc(
    session: AsyncSession,
    *,
    years: list[int],
    pdc_client: PDCClient | None = None,
    dry_run: bool = False,
    force: bool = False,
    pause_seconds: float = 0.0,
) -> HarvestSummary:
    """Archive each year's House + Senate winner cohorts (archive-only). Operates in the
    caller's transaction (the CLI commits, or rolls back on ``dry_run``).

    A mid-sweep failure aborts the whole run (nothing committed); re-run from the floor —
    closed years cache-hit, so it resumes cheaply. ``pause_seconds`` drips between years (the
    SODA analog of the WSL harvests' ``--pause-seconds``; Socrata has no central limiter)."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    adapter = PDCAdapter(
        biennium=biennium_for_date(datetime.now(UTC).date()),
        client=pdc_client or PDCClient(app_token=os.environ.get("USA_WA_PDC_APP_TOKEN")),
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
    for index, year in enumerate(years):
        if index > 0 and pause_seconds > 0:
            await asyncio.sleep(pause_seconds)
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
        "--to-year",
        type=int,
        default=None,
        help="default: the current calendar year (#121 — the biennium's seating year would "
        "miss the odd mid-biennium special cohort)",
    )
    parser.add_argument("--dry-run", action="store_true", help="harvest but roll back")
    parser.add_argument("--force", action="store_true", help="re-fetch past the freshness cache")
    parser.add_argument(
        "--pause-seconds", type=float, default=0.0, help="seconds to drip between years (SODA)"
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    # Default sweep bound = the current calendar year (#121, the SOS harvest's choice): the
    # biennium's even seating year (2024 during 2025-26) would still miss the odd special cohort.
    to_year = args.to_year or datetime.now(UTC).year
    years = election_years(args.from_year, to_year)

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            summary = await harvest_pdc(
                session,
                years=years,
                dry_run=args.dry_run,
                force=args.force,
                pause_seconds=args.pause_seconds,
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
