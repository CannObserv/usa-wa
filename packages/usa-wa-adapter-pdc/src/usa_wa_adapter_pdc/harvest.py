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

The **daily** Phase-A driver lives beside it (:mod:`usa_wa_adapter_pdc.archive_refresh`, #201)
and reuses this module's :func:`biennium_resource_ids` + :func:`archive_cohorts`: same phase,
different failure shape — the sweep is all-or-nothing over a year range, the daily pass survives
one bad cohort.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, run_job
from clearinghouse_core.logging import get_logger
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
from usa_wa_common.elections import election_years_for_biennium, senate_election_years_for_biennium
from usa_wa_common.jurisdiction import resolve_jurisdiction

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "pdc-harvest"

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


@dataclass(frozen=True)
class ArchiveSummary:
    """Counts from one **per-cohort resilient** archive pass (#201).

    Distinct from :class:`HarvestSummary` because the two passes fail differently: the
    historical sweep is all-or-nothing over a year range (a mid-sweep failure aborts, re-run
    from the floor), while the daily refresh archives a fixed cohort set and must survive one
    bad cohort — hence a ``cohorts_skipped`` tally the sweep has no use for.
    """

    cohorts: int
    cohorts_archived: int
    cohorts_skipped: int


def election_years(from_year: int, to_year: int) -> list[int]:
    """Inclusive general-election years from ``from_year`` to ``to_year`` — **every** year, not
    just even ones (#121): WA holds a general each November, and odd-year specials seat
    legislators (Nov 2025: Hunt/Krishnadasan/Zahn). A year with no legislative race archives an
    empty SODA cohort — cheap negative evidence, no error path."""
    return list(range(from_year, to_year + 1))


def biennium_resource_ids(biennium: str) -> list[str]:
    """Every winner cohort a biennium's membership can be decided by (#121), seating first.

    Both House generals (the even seating year + the odd mid-biennium special) and the three
    Senate cohorts (the two staggered evens + the odd special). Derived from the shared era
    helpers in ``usa_wa_common.elections`` — "which elections seat this biennium" is a property
    of the WA calendar, not of this source.
    """
    ids = [f"{HOUSE_WINNERS_RESOURCE_PREFIX}{y}" for y in election_years_for_biennium(biennium)]
    ids += [
        f"{SENATE_WINNERS_RESOURCE_PREFIX}{y}" for y in senate_election_years_for_biennium(biennium)
    ]
    return ids


async def _cohort_runner(
    session: AsyncSession, *, biennium: str, pdc_client: PDCClient | None
) -> AdapterRunner:
    """The archive-only runner both Phase-A passes drive (sweep + daily refresh)."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    adapter = PDCAdapter(
        biennium=biennium,
        client=pdc_client or PDCClient(app_token=os.environ.get("USA_WA_PDC_APP_TOKEN")),
    )
    return AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        fill_only=True,
    )


async def archive_cohorts(
    session: AsyncSession,
    *,
    resource_ids: list[str],
    biennium: str,
    pdc_client: PDCClient | None = None,
    force: bool = True,
) -> ArchiveSummary:
    """Archive a fixed cohort set, each in its OWN SAVEPOINT (the #106 A4 pattern).

    A raceless year is a *success* here — SODA returns an empty row set, not a 404 — so the
    guard covers only a transient Socrata failure, which must skip that cohort rather than the
    whole pass while the others still archive. Operates in the caller's transaction.
    """
    runner = await _cohort_runner(session, biennium=biennium, pdc_client=pdc_client)
    archived = skipped = 0
    for resource_id in resource_ids:
        try:
            async with session.begin_nested():
                if await runner.archive_only(resource_id, force=force):
                    archived += 1
        except httpx.HTTPError as exc:
            skipped += 1
            logger.warning(
                "pdc_cohort_skipped", extra={"resource_id": resource_id, "error": str(exc)}
            )
    return ArchiveSummary(
        cohorts=len(resource_ids), cohorts_archived=archived, cohorts_skipped=skipped
    )


async def harvest(
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
    runner = await _cohort_runner(
        session, biennium=biennium_for_date(datetime.now(UTC).date()), pdc_client=pdc_client
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


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute the sweep's own flags to the harness's shared parser."""
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
    parser.add_argument("--force", action="store_true", help="re-fetch past the freshness cache")
    parser.add_argument(
        "--pause-seconds", type=float, default=0.0, help="seconds to drip between years (SODA)"
    )


async def _harvest_job(ctx: JobContext) -> HarvestSummary:
    """Harness handler: resolve the year range and sweep."""
    args = ctx.args
    # Default sweep bound = the current calendar year (#121, the SOS harvest's choice): the
    # biennium's even seating year (2024 during 2025-26) would still miss the odd special cohort.
    to_year = args.to_year or datetime.now(UTC).year
    years = election_years(args.from_year, to_year)
    return await harvest(
        ctx.require_session(),
        years=years,
        dry_run=ctx.dry_run,
        force=args.force,
        pause_seconds=args.pause_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    """Archive the PDC winner cohorts. Exit ``0`` clean · ``1`` failed · ``2`` config."""
    return run_job(
        JOB_SLUG,
        _harvest_job,
        argv=argv,
        prog="python -m usa_wa_adapter_pdc.harvest",
        description="Archive historical PDC winner cohorts (archive-only, #79 Phase A).",
        extra_args=_add_args,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
